const C='ojo99-v5-own-network';
self.addEventListener('install',e=>e.waitUntil(caches.open(C).then(x=>x.addAll(['/','/manifest.webmanifest']))));
self.addEventListener('fetch',e=>{
  if(e.request.method==='GET'){
    e.respondWith(fetch(e.request).catch(()=>caches.match(e.request)));
  }
});
