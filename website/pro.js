const proDialog=document.querySelector('#pro-checkout');
const proForm=document.querySelector('#checkout-form');
const proError=document.querySelector('#checkout-error');
const proSubmit=document.querySelector('#checkout-submit');
const proUser=document.querySelector('#checkout-user');
const proPassword=document.querySelector('#checkout-password');
const proRegister=document.querySelector('#checkout-register');
let proPlans=new Map(),proPlan='',proToken='',proAccount='',proBusy=false;
function paymentUrl(value){
  const url=new URL(value);
  if(url.protocol!=='https:'||!['yoomoney.ru','yookassa.ru'].includes(url.hostname)||url.username||url.password||(url.port&&url.port!=='443'))throw Error('Некорректная ссылка на оплату.');
  return url.href;
}
async function proApi(path,body,token=''){
  const headers={'Content-Type':'application/json'};
  if(token)headers.Authorization='Bearer '+token;
  const response=await fetch(path,{method:'POST',headers,body:JSON.stringify(body),credentials:'omit'});
  const data=await response.json();
  if(!response.ok){const error=Error(data.error||'Не удалось подключиться. Попробуй позже.');error.status=response.status;throw error;}
  return data;
}
fetch('/api/subscription/plans',{cache:'no-store'}).then(r=>{if(!r.ok)throw Error();return r.json();}).then(data=>{
  proPlans=new Map(data.plans.map(p=>[p.id,p]));
  const enabled=data.enabled&&data.provider==='yookassa';
  document.querySelectorAll('.pro-buy').forEach(button=>button.disabled=!enabled);
  document.querySelector('#pro-payment-status').textContent=enabled?'Выбери тариф и войди в аккаунт для оплаты.':'Оплата временно недоступна. Попробуй позже.';
}).catch(()=>{document.querySelector('#pro-payment-status').textContent='Не удалось проверить доступность оплаты.';});
document.querySelectorAll('.pro-buy').forEach(button=>button.addEventListener('click',()=>{
  if(proBusy)return;
  proPlan=button.dataset.plan;
  const plan=proPlans.get(proPlan);if(!plan)return;
  document.querySelector('#checkout-plan').textContent=plan.period+' · '+plan.amount+' ₽';
  proError.textContent='';proPassword.value='';proDialog.showModal();
}));
document.querySelector('#checkout-close').addEventListener('click',()=>{if(!proBusy)proDialog.close();});
proDialog.addEventListener('close',()=>{proPassword.value='';});
proRegister.addEventListener('change',()=>{
  proToken='';proAccount='';proPassword.required=true;
  proPassword.autocomplete=proRegister.checked?'new-password':'current-password';
});
proUser.addEventListener('input',()=>{proToken='';proAccount='';proPassword.required=true;});
proForm.addEventListener('submit',async event=>{
  event.preventDefault();if(proBusy)return;
  proBusy=true;proSubmit.disabled=true;proError.textContent='Подключаемся…';
  try{
    const username=proUser.value.trim();
    if(!proToken||proAccount!==username){
      const account=await proApi('/api/auth/'+(proRegister.checked?'register':'login'),{username,password:proPassword.value});
      proToken=account.token;proAccount=username;
      proPassword.value='';proPassword.required=false;
    }
    const order=await proApi('/api/subscription/order',{plan:proPlan,email:document.querySelector('#checkout-email').value.trim(),provider:'yookassa'},proToken);
    if(order.provider!=='yookassa')throw Error('Этот способ оплаты больше не поддерживается.');
    window.location.assign(paymentUrl(order.url));
  }catch(error){if(error.status===401){proToken='';proAccount='';proPassword.required=true;}proError.textContent=error.message||'Не удалось создать платёж.';}
  finally{proBusy=false;proSubmit.disabled=false;}
});
