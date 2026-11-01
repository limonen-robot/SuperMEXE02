#include "mbed.h"
#include <cstdio>
#include <cstring>
#include <cstdlib>
#include <cerrno>
#include <cstdint>
using namespace std::chrono;
using Clock = Timeout::clock;

DigitalOut a_cw(PA_1, 0), a_ccw(PA_4, 0), b_cw(PB_0, 0), b_ccw(PA_8, 0);
DigitalOut c_cw(PB_6, 0), c_ccw(PB_7, 0), d_cw(PA_11, 0), d_ccw(PA_12, 0);
DigitalOut home_a(PB_4, 0), home_b(PB_5, 0), home_c(PA_7, 0), home_d(PA_15, 0);
DigitalOut vct(PA_0, 0);
DigitalOut *cw[] = {&a_cw, &b_cw, &c_cw, &d_cw};
DigitalOut *ccw[] = {&a_ccw, &b_ccw, &c_ccw, &d_ccw};
DigitalOut *home[] = {&home_a, &home_b, &home_c, &home_d};
BufferedSerial pc(USBTX, USBRX, 115200);
Timer uptime;
constexpr int MAX_STEPS = 32;
constexpr long MAX_PULSES = 15000, MIN_HALF_US = 10, MAX_HALF_US = 1000000;
constexpr long MAX_WAIT_US = 600000000;
constexpr uint32_t MAX_EDGE_RATE = 100000, LINK_TIMEOUT_MS = 2000, JOG_LEASE_MS = 500;
constexpr long HOME_TOTAL_US = 1020000;
enum Kind { MOVE, WAIT, HOME, TIMED_MOVE };
struct Step { Kind kind; long pulses; int64_t value; };
volatile bool running = false, timing_fault = false, repeating = false, preparing = false;
volatile bool vct_only = false, jogging = false;
bool stopping = false;
char stop_reason[24] = "USER";
uint32_t last_ping_ms = 0, last_jog_ms = 0;
long period_us = 600000, width_us = 30000, delays_us[4] = {};
bool cfg_repeat = false, cfg_home = false, cfg_vct = false;
Timeout home_off[4], prepare_timer, vct_rise_timer, vct_fall_timer;
Clock::time_point home_guard_until{}, next_vct;
bool home_guard = false;
volatile uint32_t vct_count = 0;
void stop_outputs();
void pulse_home(int i);

struct Axis {
    Timeout edge, cycle;
    Step steps[MAX_STEPS];
    int count = 0, index = 0, reported = 0;
    volatile int finished = 0;
    volatile uint32_t cycles = 0;
    volatile bool active = false;
    bool high = false, home_started = false;
    long remaining = 0, half_us = 0;
    long interval_base = 0, interval_extra = 0, interval_parts = 1, interval_fraction = 0;
    DigitalOut *pin = nullptr;
    Clock::time_point deadline, cycle_origin;

    void fault() { timing_fault = true; stop_outputs(); }
    void arm() { edge.attach_absolute(callback(this, &Axis::tick), deadline); }
    long next_interval() {
        long us = interval_base;
        interval_fraction += interval_extra;
        if (interval_fraction >= interval_parts) { interval_fraction -= interval_parts; ++us; }
        return us;
    }
    void start_step(Clock::time_point origin) {
        const Step &s = steps[finished];
        high = home_started = false;
        if (s.kind == MOVE || s.kind == TIMED_MOVE) {
            remaining = jogging ? -1 : labs(s.pulses);
            interval_parts = s.kind == TIMED_MOVE ? 2*labs(s.pulses) : 1;
            interval_base = static_cast<long>(s.value / interval_parts);
            interval_extra = static_cast<long>(s.value % interval_parts);
            interval_fraction = 0;
            half_us = next_interval();
            pin = s.pulses > 0 ? cw[index] : ccw[index];
            deadline = origin + microseconds(half_us);
        } else deadline = origin + microseconds(s.kind == WAIT ? s.value : 0);
        arm();
    }
    void complete_step() {
        ++finished;
        if (finished >= count) { active = false; return; }
        start_step(deadline);
    }
    void tick() {
        if (!active) return;
        auto late = duration_cast<microseconds>(Clock::now() - deadline).count();
        const Step &s = steps[finished];
        if (late > ((s.kind == MOVE || s.kind == TIMED_MOVE) ? 100 : 1000)) { fault(); return; }
        if (s.kind == WAIT) { complete_step(); return; }
        if (s.kind == HOME) {
            if (!home_started) {
                pulse_home(index);
                home_started = true;
                deadline = Clock::now() + microseconds(HOME_TOTAL_US);
                arm();
            } else complete_step();
            return;
        }
        if (high) {
            *pin = 0; high = false;
            if (remaining == 0) { complete_step(); return; }
        } else {
            *pin = 1; high = true;
            if (remaining > 0) --remaining;
        }
        half_us = next_interval();
        deadline += microseconds(half_us);
        if (deadline - Clock::now() < microseconds(5)) { fault(); return; }
        arm();
    }
    void start(Clock::time_point origin) {
        finished = reported = 0;
        active = count > 0;
        if (active) start_step(origin);
    }
    void cycle_tick() {
        if (!running || !repeating || timing_fault) return;
        // A final falling edge / wait may share the exact next-cycle boundary.
        if (active && deadline <= cycle_origin) { edge.detach(); tick(); }
        if (active || timing_fault || Clock::now() - cycle_origin > milliseconds(1)) { fault(); return; }
        ++cycles;
        start(cycle_origin);
        cycle_origin += microseconds(period_us);
        cycle.attach_absolute(callback(this, &Axis::cycle_tick), cycle_origin);
    }
};
Axis axes[4];

uint32_t now_ms() { return duration_cast<milliseconds>(uptime.elapsed_time()).count(); }
void send(const char *s) { pc.write(s, strlen(s)); }
bool guarding_home() { return home_guard && Clock::now() < home_guard_until; }
void home_low(DigitalOut *pin) { *pin = 0; }
void pulse_home(int i) {
    *home[i] = 1;
    const auto until = Clock::now() + microseconds(HOME_TOTAL_US);
    if (!home_guard || until > home_guard_until) home_guard_until = until;
    home_guard = true;
    // Deliberately independent: STOP must not leave a relay energized.
    home_off[i].attach(callback(home_low, home[i]), milliseconds(20));
}
void stop_outputs() {
    prepare_timer.detach();
    vct_rise_timer.detach(); vct_fall_timer.detach(); vct = 0;
    for (int i = 0; i < 4; ++i) {
        axes[i].active = false;
        axes[i].edge.detach(); axes[i].cycle.detach();
        *cw[i] = 0; *ccw[i] = 0; axes[i].high = false;
    }
}
void stopped_reply() {
    char out[64]; snprintf(out, sizeof(out), "STOPPED %s\n", stop_reason); send(out);
    stopping = false;
}
void halt(const char *reason) {
    core_util_critical_section_enter();
    stop_outputs();
    running = repeating = preparing = jogging = vct_only = false;
    timing_fault = false;
    const bool wait = guarding_home();
    core_util_critical_section_exit();
    snprintf(stop_reason, sizeof(stop_reason), "%s", reason);
    stopping = wait;
    if (wait) send("WAIT HOME\n"); else stopped_reply();
}
void vct_low() { vct = 0; }
void vct_tick() {
    if (!running || timing_fault) return;
    if (Clock::now() - next_vct > milliseconds(1)) { timing_fault = true; stop_outputs(); return; }
    const auto fall = Clock::now() + microseconds(width_us);
    if (fall >= next_vct + microseconds(period_us)) { timing_fault = true; stop_outputs(); return; }
    vct = 1; ++vct_count;
    vct_fall_timer.attach_absolute(callback(vct_low), fall);
    next_vct += microseconds(period_us);
    vct_rise_timer.attach_absolute(callback(vct_tick), next_vct);
}
void launch() {
    if (!running) return;
    preparing = false;
    const auto origin = Clock::now() + milliseconds(2);
    if (cfg_vct || repeating || vct_only) {
        next_vct = origin;
        vct_rise_timer.attach_absolute(callback(vct_tick), next_vct);
    }
    if (vct_only) { for (int i = 0; i < 4; ++i) axes[i].cycles = 0; return; }
    for (int i = 0; i < 4; ++i) {
        axes[i].cycles = 0;
        if (!axes[i].count) continue;
        if (repeating) {
            axes[i].cycle_origin = origin + microseconds(delays_us[i]);
            axes[i].cycle.attach_absolute(callback(&axes[i], &Axis::cycle_tick), axes[i].cycle_origin);
        } else axes[i].start(origin + microseconds(delays_us[i]));
    }
}
void prehome() {
    if (!running) return;
    for (int i = 0; i < 4; ++i) if (axes[i].count) pulse_home(i);
    prepare_timer.attach_absolute(callback(launch), home_guard_until + microseconds(100));
}
void clear_program() {
    for (int i = 0; i < 4; ++i) axes[i].count = axes[i].reported = axes[i].finished = 0;
}
bool integer(const char *s, long &value) {
    if (!s || !*s) return false;
    errno = 0; char *end; value = strtol(s, &end, 10);
    return errno == 0 && *end == '\0';
}
bool integer64(const char *s, int64_t &value) {
    if (!s || !*s) return false;
    errno = 0; char *end; value = strtoll(s, &end, 10);
    return errno == 0 && *end == '\0';
}
bool validate_program() {
    unsigned long rate = 0; int total = 0;
    for (int i = 0; i < 4; ++i) {
        long fastest = 0, net = 0; int64_t duration = 0;
        for (int j = 0; j < axes[i].count; ++j) {
            const Step &s = axes[i].steps[j];
            if (s.kind == MOVE || s.kind == TIMED_MOVE) {
                const long half = static_cast<long>(s.kind == MOVE ? s.value : s.value/(2*labs(s.pulses)));
                if (!fastest || half < fastest) fastest = half;
                net += s.pulses;
                duration += s.kind == TIMED_MOVE ? s.value : 2LL * labs(s.pulses) * s.value;
            } else {
                if (cfg_repeat && s.kind == HOME) { send("ERR REPEAT_HOME\n"); return false; }
                duration += s.kind == HOME ? HOME_TOTAL_US : s.value;
            }
        }
        if (fastest) rate += (1000000UL + fastest - 1) / fastest;
        if (cfg_repeat && net) { send("ERR NOT_ZERO\n"); return false; }
        if (cfg_repeat && duration > period_us) { send("ERR CYCLE_TOO_SHORT\n"); return false; }
        total += axes[i].count;
    }
    if (!total) { send("ERR EMPTY\n"); return false; }
    if (rate > MAX_EDGE_RATE) { send("ERR COMBINED_SPEED\n"); return false; }
    return true;
}
void run_program() {
    if (now_ms() - last_ping_ms > LINK_TIMEOUT_MS) { send("ERR HEARTBEAT\n"); return; }
    if (!validate_program()) return;
    running = true; timing_fault = false; repeating = cfg_repeat;
    jogging = vct_only = false; vct_count = 0;
    core_util_critical_section_enter();
    stop_outputs();
    preparing = cfg_home;
    if (preparing) prepare_timer.attach(callback(prehome), milliseconds(2)); else launch();
    core_util_critical_section_exit();
    send(repeating ? "START REPEAT\n" : "START ALL\n");
}
void add_reply(int i) {
    char out[50]; snprintf(out, sizeof(out), "ACK ADD %c %d\n", 'A' + i, axes[i].count); send(out);
}
void command(char *line) {
    char *parts[12] = {}; int n = 0;
    char *p = strtok(line, " \t");
    while (p && n < 12) { parts[n++] = p; p = strtok(nullptr, " \t"); }
    if (!n) return;
    const char *cmd = parts[0];
    if (n == 1 && !strcmp(cmd, "HELLO")) { send("READY SUPERMEXE02_MOTOR_V4\n"); return; }
    if (n == 1 && !strcmp(cmd, "PING")) { last_ping_ms = now_ms(); send("PONG\n"); return; }
    if (n == 1 && !strcmp(cmd, "STOP")) { halt("USER"); return; }
    if (n == 1 && !strcmp(cmd, "JKEEP")) { if (jogging) last_jog_ms = now_ms(); return; }
    if (running || stopping || guarding_home()) { send("ERR BUSY\n"); return; }
    if (n == 1 && !strcmp(cmd, "CLEAR")) { clear_program(); send("ACK CLEAR\n"); return; }
    if (n == 1 && !strcmp(cmd, "RUN")) { run_program(); return; }
    long values[9] = {};
    if (n == 10 && !strcmp(cmd, "CONFIG")) {
        for (int i = 0; i < 9; ++i) if (!integer(parts[i+1], values[i])) { send("ERR RANGE\n"); return; }
        if (values[0] < 10000 || values[0] > 60000000 || values[1] < 1000 || values[1] >= values[0]) { send("ERR RANGE\n"); return; }
        for (int i = 2; i < 6; ++i) if (values[i] < 0 || values[i] > MAX_WAIT_US) { send("ERR RANGE\n"); return; }
        for (int i = 6; i < 9; ++i) if (values[i] != 0 && values[i] != 1) { send("ERR RANGE\n"); return; }
        period_us = values[0]; width_us = values[1];
        for (int i = 0; i < 4; ++i) delays_us[i] = values[i+2];
        cfg_home = values[6]; cfg_repeat = values[7]; cfg_vct = values[8];
        send("ACK CONFIG\n"); return;
    }
    if (n == 3 && !strcmp(cmd, "VCT")) {
        if (!integer(parts[1], values[0]) || !integer(parts[2], values[1]) ||
            values[0] < 10000 || values[0] > 60000000 || values[1] < 1000 || values[1] >= values[0]) { send("ERR RANGE\n"); return; }
        if (now_ms() - last_ping_ms > LINK_TIMEOUT_MS) { send("ERR HEARTBEAT\n"); return; }
        period_us = values[0]; width_us = values[1]; running = vct_only = true;
        repeating = preparing = jogging = false; vct_count = 0;
        launch(); send("START VCT\n"); return;
    }
    if (n < 2 || strlen(parts[1]) != 1 || parts[1][0] < 'A' || parts[1][0] > 'D') { send("ERR COMMAND\n"); return; }
    const int i = parts[1][0] - 'A';
    const bool immediate_move = !strcmp(cmd, "MOVE"), immediate_timed = !strcmp(cmd, "MOVET"), immediate_wait = !strcmp(cmd, "WAITNOW");
    const bool immediate_home = !strcmp(cmd, "HOME"), jog = !strcmp(cmd, "JOG");
    Step step{};
    if (n == 4 && (!strcmp(cmd, "ADDT") || immediate_timed)) {
        long pulses; int64_t total;
        if (!integer(parts[2], pulses) || !integer64(parts[3], total) || !pulses ||
            pulses < -MAX_PULSES || pulses > MAX_PULSES ||
            total < 2LL*labs(pulses)*MIN_HALF_US || total > 2LL*labs(pulses)*MAX_HALF_US) {
            send("ERR RANGE\n"); return;
        }
        step = {TIMED_MOVE, pulses, total};
    } else if ((n == 4 && (!strcmp(cmd, "ADD") || immediate_move)) || (n == 3 && jog)) {
        long pulses = 1, half;
        if (jog) {
            if (!integer(parts[2], half) || !half || half < -MAX_HALF_US || half > MAX_HALF_US) { send("ERR RANGE\n"); return; }
            pulses = half > 0 ? 1 : -1; half = labs(half);
        } else if (!integer(parts[2], pulses) || !integer(parts[3], half)) { send("ERR RANGE\n"); return; }
        if (!pulses || pulses < -MAX_PULSES || pulses > MAX_PULSES || half < MIN_HALF_US || half > MAX_HALF_US) { send("ERR RANGE\n"); return; }
        step = {MOVE, pulses, half};
    } else if (n == 3 && (!strcmp(cmd, "WAIT") || immediate_wait)) {
        long us; if (!integer(parts[2], us) || us < 1 || us > MAX_WAIT_US) { send("ERR RANGE\n"); return; }
        step = {WAIT, 0, us};
    } else if (n == 2 && (!strcmp(cmd, "ZHADD") || immediate_home)) step = {HOME, 0, HOME_TOTAL_US};
    else { send("ERR COMMAND\n"); return; }
    const bool immediate = immediate_move || immediate_timed || immediate_wait || immediate_home || jog;
    if (immediate) {
        if (now_ms() - last_ping_ms > LINK_TIMEOUT_MS) { send("ERR HEARTBEAT\n"); return; }
        clear_program(); cfg_home = cfg_repeat = cfg_vct = false;
        for (auto &delay : delays_us) delay = 0;
    }
    if (axes[i].count >= MAX_STEPS) { send("ERR FULL\n"); return; }
    axes[i].steps[axes[i].count++] = step;
    if (jog) {
        running = jogging = true; repeating = preparing = vct_only = false;
        last_jog_ms = now_ms();
        core_util_critical_section_enter(); axes[i].start(Clock::now() + milliseconds(2)); core_util_critical_section_exit();
        send("START JOG\n");
    } else if (immediate) run_program(); else add_reply(i);
}

int main() {
    sleep_manager_lock_deep_sleep(); uptime.start();
    for (int i = 0; i < 4; ++i) axes[i].index = i;
    char line[128]; size_t length = 0; bool overflow = false;
    uint32_t last_status = 0;
    send("READY SUPERMEXE02_MOTOR_V4\n");
    while (true) {
        for (int i = 0; i < 128 && pc.readable(); ++i) {
            char c; if (pc.read(&c, 1) != 1) break;
            if (c == '\r' || c == '\n') {
                if (overflow) send("ERR LINE_TOO_LONG\n");
                else if (length) { line[length] = '\0'; command(line); }
                length = 0; overflow = false;
            } else if (c >= 32 && c <= 126) {
                if (length < sizeof(line)-1 && !overflow) line[length++] = c; else overflow = true;
            } else overflow = true;
        }
        if (stopping && !guarding_home()) stopped_reply();
        if (running && timing_fault) halt("TIMING");
        if (running && now_ms() - last_ping_ms > LINK_TIMEOUT_MS) halt("LINK_TIMEOUT");
        if (running && jogging && now_ms() - last_jog_ms > JOG_LEASE_MS) halt("JOG_RELEASE");
        if (running && !preparing && !repeating && !vct_only && !jogging) {
            bool any = false;
            for (int i = 0; i < 4; ++i) {
                Axis &axis = axes[i];
                while (axis.reported < axis.finished) {
                    int s = axis.reported++;
                    char out[64]; snprintf(out, sizeof(out), "STEP %c %d %ld\n", 'A'+i, s+1, labs(axis.steps[s].pulses)); send(out);
                }
                any |= axis.active;
            }
            if (!any && !timing_fault) {
                vct_rise_timer.detach(); vct_fall_timer.detach(); vct = 0;
                running = false; send("DONE ALL\n");
            }
        }
        if (running && (repeating || vct_only) && now_ms()-last_status >= 1000) {
            last_status = now_ms(); char out[100];
            snprintf(out, sizeof(out), "COUNTS %lu %lu %lu %lu %lu\n", (unsigned long)vct_count,
                     (unsigned long)axes[0].cycles, (unsigned long)axes[1].cycles,
                     (unsigned long)axes[2].cycles, (unsigned long)axes[3].cycles); send(out);
        }
        ThisThread::sleep_for(1ms);
    }
}
