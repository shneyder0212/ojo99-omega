const C='ojo99-v9-c16-operation-3of3';
self.addEventListener('install',e=>e.waitUntil(caches.open(C).then(x=>x.addAll(['/','/manifest.webmanifest']))));
self.addEventListener('activate',e=>e.waitUntil(caches.keys().then(keys=>Promise.all(keys.filter(k=>k!==C).map(k=>caches.delete(k))))));
self.addEventListener('fetch',e=>{if(e.request.method==='GET')e.respondWith(fetch(e.request).catch(()=>caches.match(e.request)))});
