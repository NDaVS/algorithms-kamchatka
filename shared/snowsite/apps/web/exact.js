const input=document.querySelector('#input'),run=document.querySelector('#run'),status=document.querySelector('#status'),output=document.querySelector('#output');
input.addEventListener('change',()=>{run.disabled=!input.files.length;});
run.addEventListener('click',async()=>{
 run.disabled=true;input.disabled=true;output.textContent='';status.textContent='Выполняется MILP…';
 try{
  const file=input.files[0];if(file.size>1024*1024)throw Error('Файл больше 1 МБ');
  const request=JSON.parse(await file.text());request.policy.preset='preserve_existing';
  const res=await fetch('/api/v1/optimize',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(request)});
  const result=await res.json();if(!res.ok)throw Error(res.status===503?'Оптимизатор занят (503).':res.status===500?'Solver недоступен или внутренний сбой. Проверьте Python и solver/requirements.txt.':`${res.status}: ${result.error}`);
  status.textContent=`${result.search.rankingStatus} / ${result.solver.status}. ${result.search.rankingStatus==='exact_top_k'?'Доказано в заданной модели.':'Точный рейтинг не доказан или нет допустимых наборов.'}`;
  output.textContent=JSON.stringify(result,null,2);
 }catch(e){status.textContent=e.message+' Автоматического перехода на эвристику нет.';}finally{run.disabled=false;input.disabled=false;}
});
