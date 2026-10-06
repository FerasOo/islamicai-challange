export class AudioCapture {
  private stream?: MediaStream;
  private context?: AudioContext;
  private node?: AudioWorkletNode;
  private source?: MediaStreamAudioSourceNode;
  private sink?: GainNode;
  private enrolling=false;
  private flushResolve?:()=>void;
  private failed=false;
  private stopping=false;
  constructor(private socket:WebSocket,private onLevel:(level:number)=>void,private onError:(message:string)=>void){}
  async start(enroll=false,profile?:{profile_name:string;profile_id?:string}){
    if(this.socket.readyState!==WebSocket.OPEN)throw new Error('انتظر اتصال الجلسة قبل تشغيل الميكروفون.');
    this.enrolling=enroll;
    try{
      this.stream=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:true,autoGainControl:true}});
      this.context=new AudioContext({sampleRate:16000});
      try {
        await this.context.audioWorklet.addModule('/audio-worklet.js');
      } catch {
        throw new Error('تعذّر تحميل مكوّن الصوت من الخادم. تحقّق من اتصال التطبيق ثم حاول مجدداً.');
      }
      this.node=new AudioWorkletNode(this.context,'microphone-processor');
      this.node.port.onmessage=(event:MessageEvent<Float32Array|{flushed:boolean}>)=>{
        if(!(event.data instanceof Float32Array)){this.flushResolve?.();return;}
        const samples=event.data;let energy=0;for(const s of samples)energy+=s*s;
        this.onLevel(Math.min(1,Math.sqrt(energy/samples.length)*8));
        if(this.socket.readyState===WebSocket.OPEN){
          if(this.socket.bufferedAmount<524288)this.socket.send(samples.buffer);
          else if(!this.failed){this.failed=true;this.onError('الاتصال متأخر. توقف التسجيل؛ أعد تشغيل الميكروفون.');void this.stop(true);}
        }
      };
      this.source=this.context.createMediaStreamSource(this.stream);
      this.sink=this.context.createGain();this.sink.gain.value=0;
      this.socket.send(JSON.stringify({type:'audio_start',enroll,sample_rate:this.context.sampleRate,...profile}));
      this.source.connect(this.node);this.node.connect(this.sink);this.sink.connect(this.context.destination);
      await this.context.resume();
    }catch(error){await this.stop(true);throw error;}
  }
  async stop(cancel=false){
    if(this.stopping||(!this.context&&!this.stream))return;
    this.stopping=true;
    if(this.node&&!cancel){
      await new Promise<void>(resolve=>{this.flushResolve=resolve;this.node!.port.postMessage({type:'flush'});setTimeout(resolve,150);});
      this.flushResolve=undefined;
    }
    this.node?.disconnect();this.source?.disconnect();this.sink?.disconnect();
    this.stream?.getTracks().forEach(t=>t.stop());
    if(this.context && this.context.state!=='closed')await this.context.close();
    if(this.socket.readyState===WebSocket.OPEN)this.socket.send(JSON.stringify({type:cancel&&this.enrolling?'cancel_enroll':'audio_stop'}));
    this.context=undefined;this.stream=undefined;this.node=undefined;this.onLevel(0);this.stopping=false;
  }
}
