const C='ojo99-v4';
self.addEventListener('install',e=>e.waitUntil(caches.open(C).then(x=>x.addAll(['/','/manifest.webmanifest']))));
self.addEventListener('fetch',e=>{if(e.request.method==='GET')e.respondWith(fetch(e.request).catch(()=>caches.match(e.request)))});
