class MicrophoneProcessor extends AudioWorkletProcessor {
  constructor(){super();this.buffer=new Float32Array(1600);this.cursor=0;this.port.onmessage=event=>{
    if(event.data.type==='flush'){
      if(this.cursor){const tail=this.buffer.slice(0,this.cursor);this.port.postMessage(tail,[tail.buffer]);this.cursor=0;}
      this.port.postMessage({flushed:true});
    }
  };}
  process(inputs,outputs){
    const input=inputs[0]?.[0];
    if(input){for(const sample of input){this.buffer[this.cursor++]=sample;if(this.cursor===1600){this.port.postMessage(this.buffer,[this.buffer.buffer]);this.buffer=new Float32Array(1600);this.cursor=0;}}}
    for(const output of outputs)for(const channel of output)channel.fill(0);
    return true;
  }
}
registerProcessor('microphone-processor',MicrophoneProcessor);
