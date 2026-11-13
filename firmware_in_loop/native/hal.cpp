#include "mbed.h"
#include <thread>
#include <mutex>
#include <condition_variable>
#include <queue>
#include <deque>
#include <vector>
#include <algorithm>
#include <stdexcept>
int firmware_main();
static_assert(sizeof(long)==4 && sizeof(float)==4,"Firmware requires 32-bit long/float");
namespace vm {
struct Stop{};
std::mutex mutex; std::condition_variable cv; std::thread worker;
int64_t us=0,wake=0; bool sleeping=false,stopping=false,failed=false;
std::deque<char> rx,tx;
int pins[16]={}; uint16_t analog[16]={}; std::function<void()> interrupts[16];
struct Edge{int64_t us;int pin,value;};std::deque<Edge> edges;
struct Job{int64_t due;uint64_t order,generation;std::shared_ptr<Event> event;std::function<void()> fn;};
struct Later{bool operator()(const Job&a,const Job&b)const{return a.due==b.due?a.order>b.order:a.due>b.due;}};
std::priority_queue<Job,std::vector<Job>,Later> jobs;uint64_t sequence=0;
int64_t now(){return us;}
void sleep(int64_t delta){std::unique_lock<std::mutex> lock(mutex);wake=us+delta;sleeping=true;cv.notify_all();cv.wait(lock,[]{return !sleeping||stopping;});if(stopping)throw Stop{};}
void write(PinName p,int value){if(pins[p]!=value){pins[p]=value;edges.push_back({us,p,value});}}
int read(PinName p){return pins[p];}
uint16_t adc(PinName p){return analog[p];}
void rise(PinName p,std::function<void()>f){interrupts[p]=f;}
bool readable(){return !rx.empty();}
ptrdiff_t receive(void*p,size_t n){size_t k=std::min(n,rx.size());for(size_t i=0;i<k;++i){((char*)p)[i]=rx.front();rx.pop_front();}return k;}
ptrdiff_t transmit(const void*p,size_t n){auto s=(const char*)p;tx.insert(tx.end(),s,s+n);return n;}
void arm(std::shared_ptr<Event>e,std::function<void()>f,int64_t at){++e->generation;jobs.push({std::max(at,us),++sequence,e->generation,e,f});}
}
#define API extern "C" __declspec(dllexport)
API int vm_start(){using namespace vm;worker=std::thread([](){try{firmware_main();}catch(const Stop&){}catch(...){std::lock_guard<std::mutex> l(mutex);failed=true;cv.notify_all();}});std::unique_lock<std::mutex> l(mutex);cv.wait(l,[]{return sleeping||failed;});return !failed;}
// APIは単一ホストスレッドからのみ呼ぶ。ファームのmainと割込みは交互に実行。
API int vm_advance(int64_t target){using namespace vm;std::unique_lock<std::mutex>l(mutex);if(target<us)return -1;
 while(!failed){while(!jobs.empty()&&jobs.top().generation!=jobs.top().event->generation)jobs.pop();
  int64_t next=jobs.empty()?wake:std::min(wake,jobs.top().due);if(next>target)break;us=next;
  if(!jobs.empty()&&jobs.top().due<=wake){auto j=jobs.top();jobs.pop();l.unlock();j.fn();l.lock();}
  else{sleeping=false;cv.notify_all();cv.wait(l,[]{return sleeping||failed;});}
 }us=target;return failed?-2:0;}
API void vm_input(const char*p,int n){vm::rx.insert(vm::rx.end(),p,p+n);}
API int vm_output(char*p,int n){int k=std::min(n,(int)vm::tx.size());for(int i=0;i<k;++i){p[i]=vm::tx.front();vm::tx.pop_front();}return k;}
API void vm_adc(int ch,int value){static PinName map[]={PA_0,PA_1,PA_4,PA_5};if(ch>=0&&ch<4)vm::analog[map[ch]]=(uint16_t)std::clamp(value,0,65535);}
API void vm_digital(int pin,int value){if(pin<0||pin>=16)return;int old=vm::pins[pin];vm::pins[pin]=value;if(!old&&value&&vm::interrupts[pin])vm::interrupts[pin]();}
API int vm_edges(int64_t* p,int capacity){int n=std::min(capacity,(int)vm::edges.size());for(int i=0;i<n;++i){auto e=vm::edges.front();vm::edges.pop_front();p[3*i]=e.us;p[3*i+1]=e.pin;p[3*i+2]=e.value;}return n;}
API void vm_stop(){using namespace vm;{std::lock_guard<std::mutex>l(mutex);stopping=true;cv.notify_all();}if(worker.joinable())worker.join();}
