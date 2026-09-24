'use strict';
const $=id=>document.getElementById(id);
let state=null, polling=false, action=false, proposalId='', catalogKey='', expiry=0;
let roi=[.15,.08,.85,.95], previewImage=null, drag=null, remembered=null, configured=false;
let rotation=0, rotating=false, previewGeneration=0;
const photo=id=>'/api/photo?id='+encodeURIComponent(id);
function notify(text){$('notice').textContent=text;$('notice').classList.add('show');setTimeout(()=>$('notice').classList.remove('show'),6000);}
async function request(path,data){const options=data===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json','X-FOX-Camera':'1'},body:JSON.stringify(data)};const response=await fetch(path,options);const result=await response.json();if(response.status===401){$('pair').hidden=false;$('studio').hidden=true;throw Error(result.error);}if(!response.ok)throw Error(result.error||'연결을 확인해 주세요.');return result;}
async function act(path,data={},success){if(action)return;action=true;document.querySelectorAll('.approve').forEach(b=>b.disabled=true);try{const result=await request(path,data);if(success)notify(success);await refresh();return result;}catch(error){notify(error.message);await refresh();}finally{action=false;document.querySelectorAll('.approve').forEach(b=>b.disabled=expiry<Date.now());}}
async function refresh(){if(polling)return;polling=true;try{const value=await request('/api/state');state=value;$('pair').hidden=true;$('studio').hidden=false;render();}catch(error){if(state){$('error').hidden=false;$('error').textContent=error.message;$('proposal').hidden=true;proposalId='';}}finally{polling=false;}}
function text(tag,value){const e=document.createElement(tag);e.textContent=value;return e;}
function render(){
 const u=state.updates;$('updateStatus').textContent=u?.enabled?'자동 업데이트 · '+u.message:'자동 업데이트 설정 전';
 $('status').textContent=state.status;$('error').hidden=!state.error;$('error').textContent=state.error;$('runLamp').classList.toggle('running',state.running);
 $('count').textContent=state.indexed+'개 사진 준비됨';$('start').disabled=state.running||state.preparing||!state.indexed;$('pause').disabled=!state.running;$('undo').disabled=!state.can_undo;
 for(const id of ['prepare','prepareTop'])$(id).disabled=state.preparing;
 $('prepareTop').textContent=state.preparing?'상품 준비 중…':'상품 준비';
 $('setup').hidden=!state.local;$('check').disabled=!state.local;$('saveCrop').disabled=!state.local;
 $('operatorHint').textContent=state.local?'방송 중에는 OBS 노트북에서 이 화면에 연결해 상품 준비·찾기·사진 전환을 할 수 있어요.':'연결됐어요. 여기서 상품 준비·찾기·사진 전환을 할 수 있어요. 메인 데스크탑의 프로그램은 계속 켜 두세요.';
 if(!configured){for(const k of ['host','port','source'])$(k).value=state.config[k];roi=state.config.roi;rotation=state.config.rotation||0;configured=true;if(!state.config.host)$('setup').open=true;draw();}
 rotationControls();
 $('password').placeholder=state.password_set?'현재 연결에 사용할 비밀번호가 입력돼 있어요':'OBS에 표시된 비밀번호';
 const current=state.products.find(p=>p.id===state.active);$('currentName').textContent=current?current.name:'기존 도우미에서 상품을 선택해 주세요.';$('currentPhoto').hidden=!current;if(current&&$('currentPhoto').dataset.id!==current.id){$('currentPhoto').src=photo(current.id);$('currentPhoto').dataset.id=current.id;}
 $('pairPin').textContent=state.local?'연결번호 '+state.pin:'메인 데스크탑에 연결됨';
 if(!$('addresses').children.length)for(const address of state.addresses){const a=text('a',address);a.href=address;a.target='_blank';a.rel='noreferrer';$('addresses').append(a);}
 const p=state.proposal;$('proposal').hidden=!p;$('waiting').hidden=!!p;
 if(!p){proposalId='';}else{
  expiry=Date.now()+p.remaining*1000;
  if(p.id!==proposalId){proposalId=p.id;$('seen').src=p.snapshot;$('candidates').replaceChildren();
   p.candidates.forEach((c,i)=>{const tile=document.createElement('article');tile.className='candidate';const image=document.createElement('img');image.src=photo(c.id);image.alt=c.name;tile.append(image,text('small','후보 '+(i+1)+' · '+c.code),text('p',c.name));const b=text('button','이 사진으로 바꾸기');b.className='approve';b.type='button';const fixedId=p.id;b.onclick=()=>act('/api/approve',{proposal:fixedId,product:c.id,confirmed:true});tile.append(b);$('candidates').append(tile);});
  }
 }
 const key=JSON.stringify(state.products.map(p=>[p.id,p.name,p.editorial]));if(key!==catalogKey){catalogKey=key;renderCatalog();}
}
function renderCatalog(){const q=$('search').value.trim().toLowerCase();$('catalog').replaceChildren();for(const p of state.products.filter(p=>(p.name+' '+p.code).toLowerCase().includes(q))){const b=document.createElement('button');b.type='button';const image=document.createElement('img');image.src=photo(p.id);image.alt='';image.loading='lazy';b.append(image,text('span',p.code+' · '+p.name));b.onclick=()=>{remembered=p.id;$('rememberPhoto').src=photo(p.id);$('rememberName').textContent=p.name;$('rememberDialog').showModal();};$('catalog').append(b);}}
function config(){return {host:$('host').value.trim(),port:Number($('port').value),source:$('source').value,password:$('password').value,roi,rotation};}
function rotationControls(){for(const id of ['rotateLeft','rotateRight'])$(id).disabled=!state?.local||rotating||!previewImage;$('rotationLabel').textContent=rotation+'° · 방향은 자동 저장돼요';}
async function rotateCamera(turn){
 if(action||rotating||!previewImage)return;
 rotating=true;rotationControls();
 try{
  const result=await act('/api/rotate',{turn});
  if(!result)return;
  rotation=result.rotation;roi=result.roi;drag=null;
  await preview();
 }finally{rotating=false;rotationControls();}
}
async function preview(){
 if(action)return;
 const generation=++previewGeneration;
 previewImage=null;draw();
 $('cameraResult').hidden=false;$('cameraResult').className='';$('cameraResult').textContent='OBS 카메라를 확인하고 있어요…';
 $('sourcePickerRow').hidden=true;
 const result=await act('/api/check');
 if(!result){$('cameraResult').textContent=state?.error||$('notice').textContent||'연결을 확인해 주세요.';return;}
 $('sources').replaceChildren();$('sourcePicker').replaceChildren();
 const prompt=text('option','카메라 소스를 선택하세요');prompt.value='';$('sourcePicker').append(prompt);
 for(const name of result.sources){const option=document.createElement('option');option.value=name;$('sources').append(option);const choice=text('option',name);choice.value=name;$('sourcePicker').append(choice);}
 $('sourcePicker').value=result.sources.includes($('source').value)?$('source').value:'';
 $('sourcePickerRow').hidden=false;
 if(!result.camera_ready){$('cameraResult').textContent=result.error||'카메라 사진을 가져오지 못했어요. 미리보기 다시 보기를 눌러 주세요.';$('cameraResult').className='error';return;}
 $('cameraResult').textContent='카메라 사진을 가져왔어요. 미리보기에서 실제 옷이 보이는지 확인하세요.';$('cameraResult').className='';
 const image=new Image();image.onload=()=>{if(generation!==previewGeneration)return;previewImage=image;const c=$('preview');c.width=image.width;c.height=image.height;draw();};image.onerror=()=>{if(generation!==previewGeneration)return;previewImage=null;draw();$('cameraResult').textContent='미리보기를 가져오지 못했어요. 미리보기 다시 보기를 눌러 주세요.';$('cameraResult').className='error';};image.src='/api/preview?t='+Date.now();
}
$('sourcePicker').onchange=()=>{if(!$('sourcePicker').value)return;$('source').value=$('sourcePicker').value;$('cameraResult').textContent='선택한 이름을 입력했어요. 설정 저장 → 카메라 연결 확인을 눌러 주세요.';};
function draw(){const c=$('preview'),ctx=c.getContext('2d');ctx.clearRect(0,0,c.width,c.height);if(previewImage)ctx.drawImage(previewImage,0,0);else{ctx.fillStyle='#e8eee9';ctx.fillRect(0,0,c.width,c.height);ctx.fillStyle='#385b49';ctx.font='20px sans-serif';ctx.fillText('카메라 연결 확인을 눌러 주세요',30,80);}ctx.strokeStyle='#edd65a';ctx.lineWidth=4;ctx.strokeRect(roi[0]*c.width,roi[1]*c.height,(roi[2]-roi[0])*c.width,(roi[3]-roi[1])*c.height);$('cropLabel').textContent='노란 사각형 안에 옷 한 벌이 들어오게 해 주세요.';rotationControls();}
function point(event){const r=$('preview').getBoundingClientRect();return [Math.max(0,Math.min(1,(event.clientX-r.left)/r.width)),Math.max(0,Math.min(1,(event.clientY-r.top)/r.height))];}
$('preview').onpointerdown=e=>{if(rotating||!previewImage)return;drag=point(e);$('preview').setPointerCapture(e.pointerId);};
$('preview').onpointermove=e=>{if(!drag)return;const p=point(e);roi=[Math.min(drag[0],p[0]),Math.min(drag[1],p[1]),Math.max(drag[0],p[0]),Math.max(drag[1],p[1])];draw();};
$('preview').onpointerup=()=>{drag=null;};
$('configForm').onsubmit=async e=>{e.preventDefault();await act('/api/config',config(),'설정을 저장했어요. 카메라 연결 확인을 눌러 주세요.');$('password').value='';};
$('saveCrop').onclick=()=>act('/api/config',config(),'옷을 볼 영역을 저장했어요.');
$('check').onclick=preview;$('previewRefresh').onclick=preview;
$('rotateLeft').onclick=()=>rotateCamera(-90);$('rotateRight').onclick=()=>rotateCamera(90);
$('prepare').onclick=$('prepareTop').onclick=()=>act('/api/prepare');$('start').onclick=()=>act('/api/start');$('pause').onclick=()=>act('/api/pause');
$('dismiss').onclick=()=>act('/api/dismiss',{proposal:proposalId});$('undo').onclick=()=>act('/api/undo',{confirmed:true});
$('refresh').onclick=()=>act('/api/refresh');$('search').oninput=()=>{if(state)renderCatalog();};
$('rememberConfirm').onclick=async()=>{if(!remembered)return;await act('/api/remember',{product:remembered,confirmed:true});$('rememberDialog').close();};
$('pairForm').onsubmit=async e=>{e.preventDefault();try{await request('/api/pair',{pin:$('pin').value.trim()});await request('/api/refresh',{});await refresh();}catch(error){notify(error.message);}};
// Keyboard presses, page load, refresh and candidate rendering never approve a switch.
setInterval(()=>{if(proposalId){const seconds=Math.max(0,Math.ceil((expiry-Date.now())/1000));$('expiry').textContent=seconds+'초 안에 확인 · 옷이 바뀌면 후보 취소';document.querySelectorAll('.approve').forEach(b=>b.disabled=action||seconds===0);}},500);
(async()=>{await refresh();if(state){try{await request('/api/refresh',{});}catch(error){notify(error.message);}await refresh();}setInterval(refresh,1500);})();
