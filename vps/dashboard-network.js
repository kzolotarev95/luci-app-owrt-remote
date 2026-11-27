/* Network map for the real Hub dashboard. Data comes from its existing refresh. */
(function (root) {
  'use strict';
  var SVG_NS = 'http://www.w3.org/2000/svg';
  var MAX_BRANCHES = 7;
  var nextMap = 0;

  function value(text, fallback) { return text == null || text === '' ? (fallback || '') : String(text); }
  function idOf(router) { return value(router.id); }
  function nameOf(router) { return value(router.name, idOf(router)); }
  function onlineOf(router) { return router.online === true; }
  function element(document, tag, className, text) {
    var node = document.createElement(tag);
    if (className) node.className = className;
    if (text != null) node.textContent = text;
    return node;
  }
  function icon(document, kind) {
    var svg = document.createElementNS(SVG_NS, 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('class', 'hubNetworkIconSvg');
    svg.setAttribute('aria-hidden', 'true');
    var path = document.createElementNS(SVG_NS, 'path');
    path.setAttribute('d', kind === 'hub' ? 'M21 12a9 9 0 1 0-18 0a9 9 0 1 0 18 0M8 14l4-9v6h4l-4 8v-5z' :
      kind === 'browser' ? 'M3 4h18v15H3zM3 8h18M6 6h.1M9 6h.1M10 12l-3 2 3 2M14 12l3 2-3 2' :
      kind === 'network' ? 'M9 3h6v6H9zM2 16h6v6H2zM16 16h6v6h-6zM12 9v4M5 16v-3h14v3' :
      'M3 13h18v7H3zM7 13V8M17 13V8M6 6a9 9 0 0 1 12 0M9 9a5 5 0 0 1 6 0M7 17h.1M11 17h.1');
    svg.appendChild(path);
    var box = element(document, 'span', 'hubNetworkIcon');
    box.appendChild(svg);
    return box;
  }
  function nodeCopy(document, eyebrow, heading, caption) {
    var copy = element(document, 'span', 'hubNetworkNodeCopy');
    copy.append(element(document, 'small', 'hubNetworkEyebrow', eyebrow),
      element(document, 'b', 'hubNetworkNodeName', heading),
      element(document, 'span', 'hubNetworkNodeCaption', caption));
    return copy;
  }

  root.createHubNetworkMap = function (host, options) {
    options = options || {};
    if (typeof host === 'string') host = root.document.querySelector(host);
    if (!host || !host.ownerDocument) throw new TypeError('Hub network map host is required');
    var document = host.ownerDocument;
    var navigator = root.navigator || {};
    var ios = /iPad|iPhone|iPod/i.test(navigator.userAgent || '') ||
      (/Macintosh|MacIntel/.test((navigator.userAgent || '') + ' ' + (navigator.platform || '')) && navigator.maxTouchPoints > 1);
    var limit = ios ? 8 : MAX_BRANCHES;
    var maxNodes = Math.min(limit, Math.max(1, Math.floor(Number(options.maxNodes) || limit)));
    var mobileQuery = ios && root.matchMedia ? root.matchMedia('(max-width:1024px)') : null;
    var onOpen = typeof options.onOpenRouter === 'function' ? options.onOpenRouter : function () {};
    var mapId = 'hub-network-' + (++nextMap);
    var map = element(document, 'section', 'hubNetwork');
    if (ios) map.dataset.ios = '1';
    map.setAttribute('aria-label', value(options.title, 'Карта подключений'));
    var header = element(document, 'div', 'hubNetworkHead');
    var title = element(document, 'h2', 'hubNetworkTitle', value(options.title, 'Карта подключений'));
    title.prepend(icon(document, 'network').firstChild);
    var legend = element(document, 'div', 'hubNetworkLegend');
    var onlineLabel = element(document, 'span', 'hubNetworkLegendItem hubNetworkLegendOnline');
    var offlineLabel = element(document, 'span', 'hubNetworkLegendItem hubNetworkLegendOffline');
    legend.append(onlineLabel, offlineLabel);
    var heading = element(document, 'div', 'hubNetworkHeading');
    heading.append(title, legend);
    var collapse = element(document, 'button', 'hubPanelToggle', 'Скрыть');
    collapse.type = 'button';
    collapse.dataset.hubPanelToggle = 'map';
    collapse.setAttribute('aria-expanded', 'true');
    collapse.setAttribute('aria-controls', 'hubMapBody');
    header.append(heading, collapse);
    var stage = element(document, 'div', 'hubNetworkStage');
    var wires = document.createElementNS(SVG_NS, 'svg');
    wires.setAttribute('viewBox', '0 0 1000 300');
    wires.setAttribute('preserveAspectRatio', 'none');
    wires.setAttribute('class', 'hubNetworkWires');
    wires.setAttribute('aria-hidden', 'true');
    var browser = element(document, 'div', 'hubNetworkNode hubNetworkBrowser');
    browser.append(icon(document, 'browser'), nodeCopy(document, 'ПАНЕЛЬ УПРАВЛЕНИЯ', 'Ваш браузер', 'Подключение к Hub'));
    var hub = element(document, 'div', 'hubNetworkNode hubNetworkHub');
    hub.append(icon(document, 'hub'), nodeCopy(document, 'ЦЕНТР УПРАВЛЕНИЯ', 'VPS · OWRT Hub', 'Статусы от агентов'));
    var branches = element(document, 'div', 'hubNetworkBranches');
    branches.id = mapId + '-routers';
    branches.setAttribute('role', 'list');
    branches.setAttribute('aria-label', 'Роутеры на схеме');
    var empty = element(document, 'p', 'hubNetworkEmpty', 'Роутеров для отображения нет.');
    var controls = element(document, 'div', 'hubNetworkControls');
    var count = element(document, 'span', 'hubNetworkCount');
    var toggle = element(document, 'button', 'hubNetworkToggle', 'Показать все');
    toggle.type = 'button';
    toggle.setAttribute('aria-controls', branches.id);
    toggle.setAttribute('aria-expanded', 'false');
    controls.append(count, toggle);
    stage.append(wires, browser, hub, branches);
    var body = element(document, 'div', 'hubPanelBody');
    body.id = 'hubMapBody';
    body.append(stage, empty, controls);
    map.append(header, body);
    host.replaceChildren(map);
    var destroyed = false;
    var pausedByUser = false;
    var signature = '';
    var viewSignature = '';
    var motionQuery = root.matchMedia ? root.matchMedia('(prefers-reduced-motion: reduce)') : null;
    var savedItems = new Map();
    var expanded = false;
    var currentRouters = [];
    var visibleRouters = [];
    var layoutFrame = 0;
    toggle.addEventListener('click', function () {
      expanded = !expanded;
      update(currentRouters);
    });

    function motionState() {
      map.dataset.paused = String(pausedByUser || body.hidden || map.hidden || document.hidden || Boolean(motionQuery && motionQuery.matches));
    }
    document.addEventListener('visibilitychange', motionState);
    if (motionQuery && motionQuery.addEventListener) motionQuery.addEventListener('change', motionState);

    function requestLayout() {
      if (!destroyed && !layoutFrame) layoutFrame = root.requestAnimationFrame(function () {
        layoutFrame = 0;
        drawPaths(visibleRouters);
      });
    }
    // iOS wraps names and rotates its viewport; use actual node positions.
    // CSS handles motion, so this observer never creates an animation loop.
    var resizeObserver = ios && root.ResizeObserver ? new root.ResizeObserver(requestLayout) : null;
    if (resizeObserver) resizeObserver.observe(stage);
    if (mobileQuery && mobileQuery.addEventListener) mobileQuery.addEventListener('change', requestLayout);

    function branchItem(router) {
      var id = idOf(router);
      var item = savedItems.get(id);
      if (!item) {
        item = element(document, 'li', 'hubNetworkBranchItem');
        item.setAttribute('role', 'listitem');
        var button = element(document, 'button', 'hubNetworkBranch');
        button.type = 'button';
        button.dataset.routerId = id;
        var copy = element(document, 'span', 'hubNetworkBranchCopy');
        var name = element(document, 'b', 'hubNetworkBranchName');
        name.appendChild(element(document, 'span', 'routerNameGradient'));
        copy.append(name, element(document, 'small', 'hubNetworkBranchId'));
        button.append(root.HubRouterPhotos ? root.HubRouterPhotos.element(document, router) : icon(document, 'router'), copy, element(document, 'span', 'hubNetworkBranchState'));
        button.addEventListener('click', function () { onOpen(id, button.hubNetworkRouter); });
        item.appendChild(button);
        savedItems.set(id, item);
      }
      var target = item.firstElementChild;
      if (root.HubRouterPhotos) {
        var model = root.HubRouterPhotos.resolve(router);
        var photo = target.querySelector('.hubRouterPhoto');
        if (!photo || photo.dataset.photoModel !== (model ? model.id : '')) target.firstElementChild.replaceWith(root.HubRouterPhotos.element(document, router));
      }
      target.hubNetworkRouter = router;
      var isOnline = onlineOf(router);
      target.classList.toggle('is-offline', !isOnline);
      target.querySelector('.hubNetworkBranchName .routerNameGradient').textContent = nameOf(router);
      target.querySelector('.hubNetworkBranchId').textContent = id;
      target.querySelector('.hubNetworkBranchState').textContent = isOnline ? 'Онлайн' : 'Офлайн';
      target.setAttribute('aria-label', nameOf(router) + ' · ' + id + ' · ' + (isOnline ? 'Онлайн' : 'Офлайн'));
      return item;
    }

    function drawPaths(routers) {
      if (mobileQuery && mobileQuery.matches) {
        var box = stage.getBoundingClientRect(), source = hub.getBoundingClientRect();
        if (!box.width || !box.height || body.hidden || map.hidden) return;
        var sx = source.left + source.width / 2 - box.left, sy = source.bottom - box.top;
        var geometry = routers.map(function (router) {
          var target = savedItems.get(idOf(router)).firstElementChild.getBoundingClientRect();
          var left = target.left + target.width / 2 < source.left + source.width / 2;
          return {x: (left ? target.right : target.left) - box.left, y: target.top + target.height / 2 - box.top, offline: !onlineOf(router)};
        });
        var mobileSignature = JSON.stringify([box.width, box.height, sx, sy, geometry]);
        if (mobileSignature === viewSignature) return;
        viewSignature = mobileSignature;
        wires.setAttribute('viewBox', '0 0 ' + box.width + ' ' + box.height);
        wires.replaceChildren();
        geometry.forEach(function (target) {
          var bend = Math.min(6, Math.abs(target.x - sx));
          var d = 'M' + sx + ' ' + sy + ' V' + (target.y - bend) +
            ' Q' + sx + ' ' + target.y + ' ' + (sx + (target.x < sx ? -bend : bend)) + ' ' + target.y + ' H' + target.x;
          wire(d, target.offline, false);
          if (!target.offline) wire(d, false, true);
        });
        return;
      }
      var rowCount = routers.length;
      var height = Math.max(245, rowCount * 59 + 26);
      var pathsSignature = routers.map(function (router) { return onlineOf(router) ? '1' : '0'; }).join('');
      if (pathsSignature === viewSignature) return;
      viewSignature = pathsSignature;
      stage.style.setProperty('--hub-network-height', height + 'px');
      wires.setAttribute('viewBox', '0 0 1000 ' + height);
      wires.replaceChildren();
      wire('M230 ' + height / 2 + ' H280', false, false);
      wire('M230 ' + height / 2 + ' H280', false, true);
      routers.forEach(function (router, index) {
        var y = rowCount <= 1 ? height / 2 : 34 + index * (height - 68) / (rowCount - 1);
        var d = 'M520 ' + height / 2 + ' C575 ' + height / 2 + ' 588 ' + y + ' 640 ' + y;
        wire(d, !onlineOf(router), false);
        if (onlineOf(router)) wire(d, false, true);
      });
    }

    function wire(d, offline, pulse) {
      var path = document.createElementNS(SVG_NS, 'path');
      path.setAttribute('class', pulse ? 'hubNetworkPulse' : 'hubNetworkWire' + (offline ? ' is-offline' : ''));
      path.setAttribute('d', d);
      if (pulse) path.style.animationDelay = String(-wires.childNodes.length * .24) + 's';
      wires.appendChild(path);
    }

    function update(routers) {
      if (destroyed) return;
      var seen = new Set();
      routers = (Array.isArray(routers) ? routers : []).filter(function (router) {
        if (!router || typeof router !== 'object' || !idOf(router) || seen.has(idOf(router))) return false;
        seen.add(idOf(router));
        return true;
      });
      currentRouters = routers;
      if (routers.length <= maxNodes) expanded = false;
      var next = JSON.stringify([expanded, routers.map(function (router) { return [idOf(router), nameOf(router), onlineOf(router), root.HubRouterPhotos ? root.HubRouterPhotos.resolve(router)?.id : '']; })]);
      if (next === signature) {
        routers.forEach(function (router) {
          var saved = savedItems.get(idOf(router));
          if (saved) saved.firstElementChild.hubNetworkRouter = router;
        });
        return;
      }
      signature = next;
      var online = routers.filter(onlineOf).length;
      onlineLabel.textContent = 'Онлайн ' + online;
      offlineLabel.textContent = 'Офлайн ' + (routers.length - online);
      map.dataset.routerCount = String(routers.length);
      map.dataset.onlineCount = String(online);
      map.dataset.offlineCount = String(routers.length - online);
      stage.hidden = !routers.length;
      empty.hidden = Boolean(routers.length);
      controls.hidden = routers.length <= maxNodes;
      toggle.textContent = expanded ? 'Скрыть все' : 'Показать все';
      toggle.setAttribute('aria-expanded', String(expanded));
      toggle.title = expanded ? 'Оставить первые ' + maxNodes + ' роутеров' : 'Показать всех роутеров из текущего списка';
      map.dataset.expanded = String(expanded);
      savedItems.forEach(function (item, id) { if (!seen.has(id)) { item.remove(); savedItems.delete(id); } });
      var primary = expanded ? routers : routers.slice(0, maxNodes);
      visibleRouters = primary;
      count.textContent = 'Показано ' + primary.length + ' из ' + routers.length;
      map.dataset.visibleCount = String(primary.length);
      primary.forEach(function (router, index) {
        var item = branchItem(router);
        if (branches.children[index] !== item) branches.insertBefore(item, branches.children[index] || null);
      });
      // Keep cached nodes for expansion without leaving hidden duplicate buttons.
      Array.from(branches.children).forEach(function (item) {
        if (!primary.some(function (router) { return idOf(router) === item.firstElementChild.dataset.routerId; })) item.remove();
      });
      drawPaths(primary);
      motionState();
    }

    function setVisible(visible) { map.hidden = visible === false; motionState(); if (ios) requestLayout(); }
    function setCollapsed(collapsed) { body.hidden = Boolean(collapsed); motionState(); if (ios) requestLayout(); }
    function setMotion(visible) { pausedByUser = visible === false; motionState(); }
    function destroy() {
      if (destroyed) return;
      destroyed = true;
      if (layoutFrame) root.cancelAnimationFrame(layoutFrame);
      if (resizeObserver) resizeObserver.disconnect();
      if (mobileQuery && mobileQuery.removeEventListener) mobileQuery.removeEventListener('change', requestLayout);
      document.removeEventListener('visibilitychange', motionState);
      if (motionQuery && motionQuery.removeEventListener) motionQuery.removeEventListener('change', motionState);
      savedItems.clear();
      map.remove();
    }
    motionState();
    update(Array.isArray(options.routers) ? options.routers : []);
    return { update: update, setVisible: setVisible, setCollapsed: setCollapsed, setMotion: setMotion, destroy: destroy, element: map, id: mapId };
  };
})(typeof window === 'undefined' ? globalThis : window);
