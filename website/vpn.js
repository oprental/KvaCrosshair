const form=document.querySelector('#bundle-form'),button=document.querySelector('#bundle-submit'),error=document.querySelector('#bundle-error'),password=document.querySelector('#bundle-password');
let busy=false;
async function api(path,body,token=''){
  const headers={'Content-Type':'application/json'};if(token)headers.Authorization='Bearer '+token;
  const response=await fetch(path,{method:'POST',headers,body:JSON.stringify(body),credentials:'omit'}),data=await response.json();
  if(!response.ok)throw Error(data.error||'Не удалось подключиться. Попробуй позже.');return data;
}
document.querySelector('#bundle-register').addEventListener('change',event=>{password.autocomplete=event.target.checked?'new-password':'current-password';});
form.addEventListener('submit',async event=>{
  event.preventDefault();if(busy)return;busy=true;button.disabled=true;error.textContent='Подключаемся…';
  document.querySelector('#bundle-result').hidden=true;
  try{
    const action=document.querySelector('#bundle-register').checked?'register':'login';
    const account=await api('/api/auth/'+action,{username:document.querySelector('#bundle-name').value.trim(),password:password.value});
    password.value='';
    const result=await api('/api/bundle/code',{},account.token);
    if(!/^[a-f0-9]{24}$/.test(result.code))throw Error('Сервер вернул некорректный код.');
    document.querySelector('#bundle-code').value=result.code;
    document.querySelector('#bundle-bot').href='https://t.me/Kwyjibovpnbot?start=kva_'+result.code;
    document.querySelector('#bundle-expiry').textContent='Действует 10 минут. Используй код только в своём приложении или боте.';
    document.querySelector('#bundle-result').hidden=false;error.textContent='';
  }catch(e){error.textContent=e.message;}finally{busy=false;button.disabled=false;}
});
document.querySelector('#bundle-copy').addEventListener('click',async()=>{
  try{await navigator.clipboard.writeText(document.querySelector('#bundle-code').value);error.textContent='Код скопирован.';}
  catch(_){document.querySelector('#bundle-code').select();error.textContent='Выделенный код можно скопировать вручную.';}
});
