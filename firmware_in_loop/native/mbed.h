#pragma once
// Windowsで実機ソースを実行するための周辺機器境界。制御ロジックはここに置かない。
#include <chrono>
#include <functional>
#include <cstdint>
#include <cstddef>
#include <memory>
enum PinName { PA_0,PA_1,PA_4,PA_5,PA_7,PA_8,PA_11,PA_12,PA_15,PB_0,PB_4,PB_5,PB_6,PB_7,USBTX,USBRX };
enum PinMode { PullDown };
namespace vm {
int64_t now(); void sleep(int64_t); void write(PinName,int); int read(PinName);
uint16_t adc(PinName); void rise(PinName,std::function<void()>);
bool readable(); ptrdiff_t receive(void*,size_t); ptrdiff_t transmit(const void*,size_t);
struct Event { uint64_t generation=0; };
void arm(std::shared_ptr<Event>,std::function<void()>,int64_t);
}
struct VirtualClock {
 using rep=int64_t; using period=std::micro; using duration=std::chrono::microseconds;
 using time_point=std::chrono::time_point<VirtualClock>; static constexpr bool is_steady=true;
 static time_point now(){return time_point(duration(vm::now()));}
};
class Timeout {
 std::shared_ptr<vm::Event> event=std::make_shared<vm::Event>();
 public: using clock=VirtualClock;
 void detach(){++event->generation;}
 template<class F> void attach_absolute(F f,clock::time_point t){vm::arm(event,f,t.time_since_epoch().count());}
 template<class F,class R,class P> void attach(F f,std::chrono::duration<R,P> t){attach_absolute(f,clock::now()+std::chrono::duration_cast<clock::duration>(t));}
};
class Timer {int64_t origin=0;public:void start(){origin=vm::now();} auto elapsed_time(){return std::chrono::microseconds(vm::now()-origin);}};
class DigitalOut {PinName pin;int value;public:DigitalOut(PinName p,int v=0):pin(p),value(v){} DigitalOut& operator=(int v){value=v;vm::write(pin,v);return *this;} operator int()const{return value;}};
class AnalogIn {PinName pin;public:AnalogIn(PinName p):pin(p){} uint16_t read_u16(){return vm::adc(pin);}};
class InterruptIn {PinName pin;public:InterruptIn(PinName p):pin(p){} void mode(PinMode){} template<class F>void rise(F f){vm::rise(pin,f);}};
class BufferedSerial {public:BufferedSerial(PinName,PinName,int){} bool readable(){return vm::readable();} ptrdiff_t read(void*p,size_t n){return vm::receive(p,n);} ptrdiff_t write(const void*p,size_t n){return vm::transmit(p,n);}};
template<class T,class R>auto callback(T* obj,R(T::*fn)()){return [=](){return (obj->*fn)();};}
template<class F,class...A>auto callback(F f,A...a){return [=](){return f(a...);};}
namespace ThisThread {template<class R,class P>void sleep_for(std::chrono::duration<R,P>d){vm::sleep(std::chrono::duration_cast<std::chrono::microseconds>(d).count());}}
inline void sleep_manager_lock_deep_sleep(){}
inline void core_util_critical_section_enter(){}
inline void core_util_critical_section_exit(){}
