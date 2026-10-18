import json,os,soundfile as sf,numpy as np
from kokoro_onnx import Kokoro
from lines import V,ORDER
h=os.path.expanduser('~/.cache/hyperframes/tts/')
k=Kokoro(h+'models/kokoro-v1.0.onnx',h+'voices/voices-v1.0.bin')
os.makedirs('aud',exist_ok=True); D={}
for sc in ORDER:
    for i,(t,_) in enumerate(V[sc]):
        s,sr=k.create(t,voice='af_heart',speed=0.96,lang='en-us'); sf.write(f'aud/{sc}_{i}.wav',s,sr); D[f'{sc}_{i}']=len(s)/sr
json.dump(D,open('dur.json','w'),indent=1); print(D)
