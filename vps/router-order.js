/* One account's order is stored by Hub and shared with the native app. */
(function () {
  'use strict';
  let pending = false, drag = null, frame = 0, lastX = 0, lastY = 0;
  const escape = value => String(value).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  let status, statusTimer;
  function announce(message, error = false) {
    if (!status) {
      status = document.createElement('div'); status.className = 'hubOrderStatus';
      status.setAttribute('role', 'status'); status.setAttribute('aria-live', 'polite');
      document.body.append(status);
    }
    clearTimeout(statusTimer); status.hidden = false;
    status.textContent = message; status.dataset.error = String(error);
    statusTimer = setTimeout(() => { status.hidden = true; }, error ? 7000 : 3500);
  }
  const cards = () => [...document.querySelectorAll('#cards article[data-router-id]')].filter(card => card.getClientRects().length);
  function peers(card) {
    const group = card?.closest('.routerGroupCards');
    return cards().filter(other => !group || other.closest('.routerGroupCards') === group);
  }
  function controls(id) {
    const key = escape(id);
    const icon = path => '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="'+path+'"/></svg>';
    return '<div class="hubOrderControls" role="group" aria-label="Порядок карточки '+key+'">'+
      '<button type="button" data-router-move="previous" data-order-id="'+key+'" aria-label="Переместить '+key+' раньше" title="Переместить раньше">'+icon('M12 20V4m-6 6 6-6 6 6')+'</button>'+
      '<button type="button" class="hubOrderHandle" data-router-drag="'+key+'" aria-label="Перетащить '+key+'" title="Потяните, чтобы переместить карточку">'+icon('M12 3v18M3 12h18M9 6l3-3 3 3M9 18l3 3 3-3M6 9l-3 3 3 3m12-6 3 3-3 3')+'</button>'+
      '<button type="button" data-router-move="next" data-order-id="'+key+'" aria-label="Переместить '+key+' позже" title="Переместить позже">'+icon('M12 4v16m-6-6 6 6 6-6')+'</button></div>';
  }
  function sync() {
    for (const card of cards()) {
      const list = peers(card), index = list.indexOf(card);
      card.querySelectorAll('[data-router-move]').forEach(button => {
        button.disabled = pending || (button.dataset.routerMove === 'previous' ? index === 0 : index === list.length - 1);
      });
      card.querySelectorAll('[data-router-drag]').forEach(button => { button.disabled = pending || list.length < 2; });
    }
  }
  async function move(id, target, placement, focus) {
    if (pending || id === target) return;
    pending = true; sync(); announce('Сохраняем порядок…');
    const abort = new AbortController(), timeout = setTimeout(() => abort.abort(), 10000);
    try {
      const response = await fetch('/api/routers/order', {method:'POST', cache:'no-store', signal:abort.signal,
        headers:{'Content-Type':'application/json','X-Router-Order':'1'},
        body:JSON.stringify({id,target,placement,base_order:sortHubRouterList(window.ROUTERS || []).map(row => row.id)})});
      const data = await response.json();
      if (!response.ok || !Array.isArray(data.router_order)) throw new Error(data.error || 'Не удалось сохранить порядок');
      const rank = new Map(data.router_order.map((key,index) => [key,index]));
      (window.ROUTERS || []).forEach(row => { row.display_order = rank.has(row.id) ? rank.get(row.id) : null; });
      // Ignore any poll that was already in flight before this save.
      routerPollVersion++;
      pending = false;
      renderRouterView();
      if (focus) {
        const button = [...document.querySelectorAll('[data-order-id]')]
          .find(item => item.dataset.orderId === id && item.dataset.routerMove === focus);
        const handle = [...document.querySelectorAll('[data-router-drag]')].find(item => item.dataset.routerDrag === id);
        (button?.disabled ? handle : button)?.focus({preventScroll:true});
      }
      announce('Порядок карточек сохранён');
    } catch (error) { announce(error.name === 'AbortError' ? 'Не удалось подтвердить сохранение. Проверьте соединение.' : error.message, true); }
    finally { clearTimeout(timeout); pending = false; sync(); flushDeferredRouterRender(); }
  }
  document.addEventListener('click', event => {
    const button = event.target.closest('[data-router-move]');
    if (!button || button.disabled) return;
    event.preventDefault(); event.stopPropagation();
    const list = peers(button.closest('article')), index = list.indexOf(button.closest('article'));
    const previous = button.dataset.routerMove === 'previous', target = list[index + (previous ? -1 : 1)];
    if (target) move(button.dataset.orderId, target.dataset.routerId, previous ? 'before' : 'after', button.dataset.routerMove);
  }, true);
  function markTarget() {
    if (!drag) return;
    document.querySelectorAll('.hubOrderTarget').forEach(card => card.classList.remove('hubOrderTarget'));
    drag.target = null;
    const hit = document.elementFromPoint(lastX, lastY)?.closest('#cards article[data-router-id]');
    if (hit && hit !== drag.card && peers(drag.card).includes(hit)) {
      const box = hit.getBoundingClientRect();
      drag.target = hit; drag.placement = lastY < box.top + box.height / 2 ? 'before' : 'after';
      hit.classList.add('hubOrderTarget'); hit.dataset.orderPlacement = drag.placement;
    }
  }
  function scrollDrag() {
    frame = 0;
    if (!drag?.active) return;
    const delta = lastY < 80 ? -12 : lastY > innerHeight - 80 ? 12 : 0;
    if (delta) { window.scrollBy(0, delta); markTarget(); }
    frame = requestAnimationFrame(scrollDrag);
  }
  document.addEventListener('pointerdown', event => {
    const handle = event.target.closest('[data-router-drag]');
    if (!handle || handle.disabled || pending || event.button !== 0) return;
    drag = {pointer:event.pointerId, handle, card:handle.closest('article'), x:event.clientX,y:event.clientY,active:false};
    handle.setPointerCapture(event.pointerId); lastX = event.clientX; lastY = event.clientY;
    event.preventDefault();
  });
  document.addEventListener('pointermove', event => {
    if (!drag || event.pointerId !== drag.pointer) return;
    lastX = event.clientX; lastY = event.clientY;
    if (!drag.active && Math.hypot(lastX-drag.x,lastY-drag.y) > 6) {
      drag.active = true; drag.card.classList.add('hubOrderDragging');
      frame = requestAnimationFrame(scrollDrag); announce('Перетащите карточку на нужное место');
    }
    if (drag.active) { event.preventDefault(); markTarget(); }
  }, {passive:false});
  function end(event, cancel = false) {
    if (!drag || (event?.pointerId != null && event.pointerId !== drag.pointer)) return;
    const current = drag; drag = null; cancelAnimationFrame(frame); frame = 0;
    if (current.handle.hasPointerCapture(current.pointer)) current.handle.releasePointerCapture(current.pointer);
    document.querySelectorAll('.hubOrderDragging,.hubOrderTarget').forEach(card => card.classList.remove('hubOrderDragging','hubOrderTarget'));
    if (!cancel && current.active && current.target) move(current.card.dataset.routerId,current.target.dataset.routerId,current.placement);
    else { if (current.active) announce('Перемещение отменено'); flushDeferredRouterRender(); }
  }
  document.addEventListener('pointerup', event => end(event));
  document.addEventListener('pointercancel', event => end(event,true));
  document.addEventListener('lostpointercapture', event => end(event,true));
  document.addEventListener('keydown', event => { if (event.key === 'Escape' && drag) { event.preventDefault(); end(null,true); } });
  window.addEventListener('blur', () => end(null,true));
  window.addEventListener('pagehide', () => end(null,true));
  window.HubRouterOrder = {controls,sync,busy:() => Boolean(drag || pending)};
})();
