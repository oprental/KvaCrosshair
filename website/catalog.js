const grid = document.querySelector('#catalog-grid');
function draw(canvas, packageData) {
  const s=packageData.settings, ctx=canvas.getContext('2d');
  const cx=150, cy=72, color=s.color, length=s.size, gap=s.gap;
  ctx.globalAlpha=s.opacity/100;
  if(s.style==='Изображение') {
    const image=new Image();
    image.onload=()=>{const ratio=Math.min(length*2/image.width,length*2/image.height,280/image.width,130/image.height);const w=image.width*ratio,h=image.height*ratio;ctx.drawImage(image,cx-w/2,cy-h/2,w,h)};
    image.src=packageData.image_gif?'data:image/gif;base64,'+packageData.image_gif:'data:image/png;base64,'+packageData.image_png;
    return;
  }
  const lines=[];
  if(s.style==='Крест'||s.style==='Т-образный') {
    lines.push([cx-gap-length,cy,cx-gap,cy],[cx+gap,cy,cx+gap+length,cy],[cx,cy+gap,cx,cy+gap+length]);
    if(s.style==='Крест') lines.push([cx,cy-gap-length,cx,cy-gap]);
  }
  for(const line of lines) {
    if(s.outline) {ctx.strokeStyle='#000';ctx.lineWidth=s.thickness+2;ctx.beginPath();ctx.moveTo(line[0],line[1]);ctx.lineTo(line[2],line[3]);ctx.stroke()}
    ctx.strokeStyle=color;ctx.lineWidth=s.thickness;ctx.beginPath();ctx.moveTo(line[0],line[1]);ctx.lineTo(line[2],line[3]);ctx.stroke();
  }
  if(s.style==='Круг') {
    if(s.outline){ctx.lineWidth=s.thickness+2;ctx.strokeStyle='#000';ctx.beginPath();ctx.arc(cx,cy,length,0,Math.PI*2);ctx.stroke()}
    ctx.lineWidth=s.thickness;ctx.strokeStyle=color;ctx.beginPath();ctx.arc(cx,cy,length,0,Math.PI*2);ctx.stroke();
  }
  if(s.dot||s.style==='Точка'){
    const radius=Math.max(s.thickness/2,1);
    if(s.outline){ctx.fillStyle='#000';ctx.beginPath();ctx.arc(cx,cy,radius+1,0,Math.PI*2);ctx.fill()}
    ctx.fillStyle=color;ctx.beginPath();ctx.arc(cx,cy,radius,0,Math.PI*2);ctx.fill();
  }
}
fetch('/api/crosshairs').then(response=>{if(!response.ok)throw Error();return response.json()}).then(data=>{
  grid.replaceChildren();
  if(!data.items.length){grid.textContent='Стань первым: опубликуй прицел из приложения.';return}
  for(const p of data.items.slice(0,6)){
    const card=document.createElement('article');card.className='catalog-card';
    const canvas=document.createElement('canvas');canvas.width=300;canvas.height=145;canvas.setAttribute('aria-label','Предпросмотр прицела '+p.settings.name);
    const tag=document.createElement('span');tag.className='tag';tag.textContent=p.settings.style;
    const title=document.createElement('h3');title.textContent=p.settings.name;
    const author=document.createElement('p');author.textContent=p.author||'Без автора';
    if(p.premium && /^#[0-9a-f]{6}$/i.test(p.nick_color||'')){author.style.color=p.nick_color;author.textContent+=' ★'}
    card.append(canvas,tag,title,author);grid.append(card);draw(canvas,p);
  }
}).catch(()=>{grid.replaceChildren();const message=document.createElement('p');message.className='catalog-message';message.textContent='Каталог временно недоступен. Попробуй открыть его позже в приложении.';grid.append(message)});
