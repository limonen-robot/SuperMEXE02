#include "mbed.h"
#include <cstdio>
#include <cstring>
#include <cstdlib>
#include <cerrno>
using namespace std::chrono;
AnalogIn a(PA_0), b(PA_1), c(PA_4), d(PA_5);
AnalogIn *inputs[] = {&a,&b,&c,&d};
InterruptIn vct(PB_0);
BufferedSerial pc(USBTX,USBRX,115200);
Timer uptime;
struct TriggerJob { uint64_t due, trigger; uint32_t beat; uint8_t channel; bool used; };
TriggerJob pending_jobs[128] = {};
struct Beat { uint64_t time; uint32_t id; };
Beat beats[32];
volatile unsigned head=0, tail=0;
volatile bool overflow=false;
volatile uint32_t beat_count=0;
uint32_t delays[4] = {310000,310000,310000,310000};
unsigned mask=0;
uint64_t micros() { return duration_cast<microseconds>(uptime.elapsed_time()).count(); }
void vct_edge() {
    unsigned next=(head+1)%32;
    if(next==tail){overflow=true;return;}
    beats[head]={micros(),++beat_count}; head=next;
}
void send(const char *s){pc.write(s,strlen(s));}
void configure(char *line){
    if(!strcmp(line,"HELLO")){send("READY SUPERMEXE02_SENSOR_V1\n");return;}
    unsigned m; unsigned long x[4]; char extra;
    if(sscanf(line,"SENSORCFG %u %lu %lu %lu %lu %c",&m,&x[0],&x[1],&x[2],&x[3],&extra)!=5 || m>15){send("ERR CONFIG\n");return;}
    for(int i=0;i<4;++i)if(x[i]>60000000){send("ERR RANGE\n");return;}
    core_util_critical_section_enter();
    mask=m; for(int i=0;i<4;++i)delays[i]=x[i];
    tail=head; overflow=false; for(auto &e:pending_jobs)e.used=false;
    core_util_critical_section_exit();
    send("ACK SENSORCFG\n");
}
int main(){
    sleep_manager_lock_deep_sleep(); uptime.start(); vct.mode(PullDown); vct.rise(callback(vct_edge));
    send("READY SUPERMEXE02_SENSOR_V1\n");
    uint64_t next_sample=micros()+10000; uint32_t sequence=0;
    char line[100]; unsigned length=0; bool bad=false;
    while(true){
        for(int n=0;n<100 && pc.readable();++n){
            char ch; if(pc.read(&ch,1)!=1)break;
            if(ch=='\n'||ch=='\r'){if(length&&!bad){line[length]=0;configure(line);}length=0;bad=false;}
            else if(length<sizeof(line)-1)line[length++]=ch; else bad=true;
        }
        if(overflow){overflow=false;send("FAULT TRIGGER_QUEUE\n");}
        while(tail!=head){
            Beat beat=beats[tail]; tail=(tail+1)%32;
            for(int ch=0;ch<4;++ch)if(mask&(1<<ch)){
                bool found=false;
                for(auto &e:pending_jobs)if(!e.used){e={beat.time+delays[ch],beat.time,beat.id,(uint8_t)ch,true};found=true;break;}
                if(!found)send("FAULT TRIGGER_QUEUE\n");
            }
        }
        for(auto &e:pending_jobs)if(e.used && micros()>=e.due){
            uint64_t sampled=micros(); unsigned value=inputs[e.channel]->read_u16();
            char out[120]; snprintf(out,sizeof(out),"T %lu %u %llu %llu %u\n",(unsigned long)e.beat,
                (unsigned)e.channel,(unsigned long long)e.due,(unsigned long long)sampled,value);send(out);e.used=false;
        }
        uint64_t now=micros();
        if(now>=next_sample){
            unsigned values[4];for(int i=0;i<4;++i)values[i]=inputs[i]->read_u16();
            char out[110];snprintf(out,sizeof(out),"S %lu %llu %u %u %u %u\n",(unsigned long)++sequence,
                (unsigned long long)now,values[0],values[1],values[2],values[3]);send(out);
            next_sample+=10000;
            if(micros()>next_sample+10000){send("FAULT SAMPLE_OVERRUN\n");next_sample=micros()+10000;}
        }
        ThisThread::sleep_for(1ms);
    }
}
