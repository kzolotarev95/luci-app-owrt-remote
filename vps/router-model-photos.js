/* Photos are bundled on the Hub; hardware is read only from agent model fields. */
(function (root) {
  'use strict';
  const catalog = Array.isArray(root.HUB_ROUTER_MODEL_CATALOG) ? root.HUB_ROUTER_MODEL_CATALOG : [];
  const normalize = value => String(value || '').toLowerCase().normalize('NFKD')
    .replace(/\([^)]*(?:openwrt|u-?boot|layout|stock|ubi)[^)]*\)/g, '')
    .replace(/[^a-z0-9]/g, '');
  const aliases = [];
  catalog.forEach(model => {
    if (!model.photo || !/^\/router-model-photos\/[a-z0-9.-]+\.webp$/.test(model.photo)) return;
    for (const alias of model.aliases || [model.model]) {
      const key = normalize(alias);
      if (key) aliases.push({key, model});
    }
  });
  aliases.sort((a,b) => b.key.length-a.key.length);
  const cache = new Map();
  const loaded = new Set(), failed = new Set();
  function resolve(router) {
    const status = router && typeof router.status === 'object' ? router.status : {};
    const hardware = [status.model, status.board, router && router.model];
    for (const value of hardware) {
      if (typeof value !== 'string' || /^(?:unknown|generic|openwrt|unavailable|неизвестно)?$/i.test(value.trim())) continue;
      const key = normalize(value);
      if (!key) continue;
      if (cache.has(key)) return cache.get(key);
      const match = aliases.find(alias => key === alias.key);
      const model = match ? match.model : null;
      // Bound the cache even if agents keep sending different model strings.
      if (cache.size >= 512) cache.clear();
      cache.set(key, model);
      return model;
    }
    return null;
  }
  const fallback = '<svg class="hubRouterPhotoFallback" viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="12" width="18" height="8" rx="2"/><path d="M7 12V5m10 7V5M7 16h.01M11 16h.01M15 16h2"/></svg>';
  function element(document, router, className) {
    const node = document.createElement('span');
    node.className = 'hubRouterPhoto ' + (className || '');
    node.innerHTML = fallback;
    const model = resolve(router);
    node.dataset.photoModel = model ? model.id : '';
    if (model && !failed.has(model.photo)) {
      if (loaded.has(model.photo)) node.classList.add('has-photo');
      const img = document.createElement('img');
      img.className = 'hubRouterPhotoImage'; img.alt = model.model; img.title = model.model;
      img.width = 320; img.height = 240; img.loading = 'lazy'; img.decoding = 'async'; img.draggable = false;
      img.src = model.photo; node.append(img);
    }
    return node;
  }
  function escape(value) { return String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
  function markup(router, className) {
    const model = resolve(router);
    const ready = model && loaded.has(model.photo) && !failed.has(model.photo);
    return '<span class="hubRouterPhoto '+escape(className || '')+(ready ? ' has-photo' : '')+'" data-photo-model="'+escape(model ? model.id : '')+'">'+fallback+
      (model && !failed.has(model.photo) ? '<img class="hubRouterPhotoImage" src="'+escape(model.photo)+'" alt="'+escape(model.model)+'" title="'+escape(model.model)+'" width="320" height="240" loading="lazy" decoding="async" draggable="false">' : '')+'</span>';
  }
  function replaceCards(container, html) {
    // Keeping an IMG object alone is insufficient for WebKit: moving it into
    // a detached template still drops the lazy image's presented layer. Patch
    // the connected group -> slot -> card -> name -> photo chain in place.
    function key(node) {
      if (node.nodeType !== 1) return '';
      if (node.matches('.routerGroupSection')) return 'group:' + node.dataset.routerGroupSection;
      if (node.matches('.routerGroupCards')) return 'group-cards';
      if (node.matches('.routerCardSlot')) return 'slot:' + node.querySelector('article[data-router-id]')?.dataset.routerId;
      if (node.matches('article[data-router-id]')) return 'card:' + node.dataset.routerId;
      if (node.matches('.nameRow')) return 'name';
      if (node.matches('.hubRouterPhoto')) return 'photo:' + node.dataset.photoModel + ':' + (node.querySelector('img')?.getAttribute('src') || '');
      return '';
    }
    function patch(parent, fresh) {
      const saved = new Map();
      Array.from(parent.childNodes).forEach(node => { const id = key(node); if (id) saved.set(id, node); });
      const wanted = new Set(Array.from(fresh.childNodes, key).filter(Boolean));
      let cursor = parent.firstChild;
      Array.from(fresh.childNodes).forEach(next => {
        const id = key(next);
        let current = id && saved.get(id);
        if (current && current.nodeName !== next.nodeName) current = null;
        if (current) {
          // Photo load/error state belongs to the connected element.
          if (!current.matches('.hubRouterPhoto')) {
            Array.from(current.attributes).forEach(attr => {
              if (!next.hasAttribute(attr.name)) current.removeAttribute(attr.name);
            });
            Array.from(next.attributes).forEach(attr => {
              if (current.getAttribute(attr.name) !== attr.value) current.setAttribute(attr.name, attr.value);
            });
            patch(current, next);
          }
        } else if (!id && cursor && !key(cursor)) {
          current = cursor;
          if (!cursor.isEqualNode(next)) {
            parent.replaceChild(next, cursor);
            current = next;
            cursor = next;
          }
        } else current = next;
        // Discard replaced siblings before the next retained ancestor; do not
        // move a connected name/photo chain merely because its status changed.
        while (cursor && cursor !== current && (!key(cursor) || !wanted.has(key(cursor)))) {
          const obsolete = cursor;
          cursor = cursor.nextSibling;
          obsolete.remove();
        }
        if (current !== cursor) parent.insertBefore(current, cursor);
        cursor = current.nextSibling;
      });
      while (cursor) { const next = cursor.nextSibling; cursor.remove(); cursor = next; }
    }
    const template = container.ownerDocument.createElement('template');
    template.innerHTML = html;
    patch(container, template.content);
  }
  root.document.addEventListener('load', event => {
    if (event.target.matches && event.target.matches('.hubRouterPhotoImage')) {
      const src = event.target.getAttribute('src'); loaded.add(src); failed.delete(src);
      event.target.parentElement.classList.add('has-photo');
    }
  }, true);
  root.document.addEventListener('error', event => {
    if (event.target.matches && event.target.matches('.hubRouterPhotoImage')) {
      const src = event.target.getAttribute('src'); failed.add(src); loaded.delete(src);
      event.target.hidden = true; event.target.parentElement.classList.remove('has-photo');
    }
  }, true);
  root.HubRouterPhotos = {resolve, element, markup, replaceCards};
})(window);
