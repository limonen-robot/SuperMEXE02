// Included after the motion engine. No allocation, serial writes, or PID work in pulse IRQs.
struct Feedback {
    bool configured=false, data=false, started=false, position_known=false;
    int axis=0, polarity=-1;
    long period=7500000, initial=0, high=13000, step=1500, half=250;
    float kp=13,ki=0.05f,kd=1,dead=1.8f,ratio=0.03f,tau=5;
    float target=0,current=0,integral=0,previous=0,derivative=0;
    uint64_t received=0,updated=0;
};
Feedback feedback[3];
bool fb_initial_complete=false, fb_priming=false;
uint64_t fb_last_status=0;
uint64_t fb_us(){return duration_cast<microseconds>(uptime.elapsed_time()).count();}
void fb_report(bool force){
    const uint64_t now=fb_us();
    if(!force&&now-fb_last_status<1000000)return;
    fb_last_status=now;
    for(int i=0;i<3;++i)if(feedback[i].configured){auto &f=feedback[i];char out[80];
        int state=!f.position_known?(running?1:5):!running?0:fb_priming?2:f.started?4:3;
        snprintf(out,sizeof(out),"FBSTAT %d %d %ld\n",i,state,(long)axes[f.axis].position);send(out);
    }
}
bool fb_any(){for(auto &f:feedback)if(f.configured)return true;return false;}
bool fb_axis(int axis){for(auto &f:feedback)if(f.configured&&f.axis==axis)return true;return false;}
void fb_clear(){for(auto &f:feedback)f=Feedback{};fb_mode=fb_initial_complete=fb_priming=false;}
void fb_halt(){fb_mode=fb_priming=false;for(auto &f:feedback){f.started=f.data=false;}}
float fb_clamp(float v,float lo,float hi){return v<lo?lo:v>hi?hi:v;}
bool fb_number(const char *s,float &v){errno=0;char *end;v=strtof(s,&end);return *s&&!*end&&!errno&&std::isfinite(v)&&fabsf(v)<=1e9f;}
void fb_move(Feedback &f,long delta){
    if(!delta)return;
    Axis &a=axes[f.axis];a.steps[0]={MOVE,delta,f.half};a.count=1;
    core_util_critical_section_enter();a.start(Clock::now()+milliseconds(2));core_util_critical_section_exit();
}
void fb_begin(){
    preparing=true;fb_priming=true;
    for(auto &f:feedback)if(f.configured){
        axes[f.axis].position=0;f.position_known=true;f.integral=f.derivative=0;f.started=false;
        fb_move(f,f.initial);
    }
}
bool fb_validate(unsigned long &rate){
    bool used[4]={};
    for(auto &f:feedback)if(f.configured){
        if(used[f.axis]||axes[f.axis].count){send("ERR AXIS_CONFLICT\n");return false;}
        used[f.axis]=true;rate+=(1000000UL+f.half-1)/f.half;
    }
    return true;
}
bool fb_command(char **p,int n){
    if(!strcmp(p[0],"FBVAL")){
        long id;float target,current;
        if(n!=4||!integer(p[1],id)||id<0||id>2||!fb_number(p[2],target)||!fb_number(p[3],current))send("ERR FB_VALUE\n");
        else if(running&&fb_mode&&feedback[id].configured){auto &f=feedback[id];f.target=target;f.current=current;f.received=fb_us();f.data=true;}
        return true;
    }
    if(strcmp(p[0],"FBCFG"))return false;
    if(running||stopping||guarding_home()){send("ERR BUSY\n");return true;}
    long id,v[7];float k[6];
    if(n!=16||!integer(p[1],id)||id<0||id>2||strlen(p[2])!=1||p[2][0]<'A'||p[2][0]>'D'){send("ERR FB_CONFIG\n");return true;}
    for(int i=0;i<7;++i)if(!integer(p[i+3],v[i])){send("ERR FB_CONFIG\n");return true;}
    for(int i=0;i<6;++i)if(!fb_number(p[i+10],k[i])||k[i]<0){send("ERR FB_CONFIG\n");return true;}
    if(v[0]<100000||v[0]>600000000||v[2]!=0||v[3]<-15000||v[3]>15000||v[1]<-15000||v[1]>v[3]||
       v[4]<1||v[4]>15000||v[5]<10||v[5]>1000000||(v[6]!=1&&v[6]!=-1)||k[4]>1||k[5]>600){send("ERR FB_CONFIG\n");return true;}
    for(int i=0;i<3;++i)if(i!=id&&feedback[i].configured&&feedback[i].axis==p[2][0]-'A'){send("ERR AXIS_CONFLICT\n");return true;}
    Feedback f;f.configured=true;f.axis=p[2][0]-'A';f.period=v[0];f.initial=v[1];f.high=v[3];
    f.step=v[4];f.half=v[5];f.polarity=v[6];f.kp=k[0];f.ki=k[1];f.kd=k[2];f.dead=k[3];f.ratio=k[4];f.tau=k[5];
    feedback[id]=f;char out[40];snprintf(out,sizeof(out),"ACK FBCFG %ld\n",id);send(out);return true;
}
void fb_poll(){
    if(!running||!fb_mode)return;
    const uint64_t now=fb_us();
    fb_report(false);
    if(fb_priming){
        bool moving=false;for(auto &f:feedback)if(f.configured)moving|=axes[f.axis].active;
        if(!moving){
            for(auto &f:feedback){f.data=f.started=false;}
            fb_priming=false;fb_initial_complete=true;launch();send("FBREADY ALL\n");
        }
        return;
    }
    if(preparing||!fb_initial_complete)return;
    for(auto &f:feedback)if(f.configured){
        if(f.data&&now-f.received>1500000){halt("FB_DATA_TIMEOUT");return;}
        if(!f.data)continue;
        if(!f.started){f.started=true;f.updated=now;f.previous=f.current;f.integral=f.derivative=0;continue;}
        if(now-f.updated<(uint64_t)f.period||axes[f.axis].active)continue;
        float dt=(now-f.updated)/1000000.0f;f.updated=now;
        float error=f.target-f.current;
        float raw=(f.current-f.previous)/dt;f.previous=f.current;
        float alpha=f.tau>0?dt/(f.tau+dt):1;f.derivative+=alpha*(raw-f.derivative);
        float band=fmaxf(f.dead,fabsf(f.target)*f.ratio);
        if(fabsf(error)<=band){f.integral*=fmaxf(0,1-0.2f*dt);continue;}
        float old_i=f.integral;
        float limit=f.ki>0?f.step/f.ki:0;
        f.integral=fb_clamp(f.integral+error*dt,-limit,limit);
        float u=f.polarity*(f.kp*error+f.ki*f.integral-f.kd*f.derivative);
        if(!std::isfinite(u)){halt("FB_NUMERIC");return;}
        float limited=fb_clamp(u,-f.step,f.step);
        long position=axes[f.axis].position;
        // Round the correction before adding, preserving one-pulse accuracy far from zero.
        const long delta=lroundf(limited);
        const int64_t requested=(int64_t)position+delta;
        const int64_t destination=requested>f.high?f.high:requested;
        if(destination<INT32_MIN){halt("FB_POSITION_RANGE");return;}
        if(limited!=u||requested>f.high)f.integral=old_i;
        fb_move(f,static_cast<long>(destination-position));
    }
}
