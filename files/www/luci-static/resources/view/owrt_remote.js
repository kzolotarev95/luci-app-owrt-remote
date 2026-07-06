'use strict';
'require view';
'require fs';

return view.extend({
	load: function() {
		return fs.read('/etc/owrt-remote/web.key').catch(function() {
			return '';
		});
	},

	render: function(key) {
		key = (key || '').trim();

		if (!key) {
			return E('div', { 'class': 'cbi-map' }, [
				E('h2', {}, _('OpenWrt Remote Hub')),
				E('div', { 'class': 'alert-message warning' }, [
					_('Web key not found. Reinstall the module or create /etc/owrt-remote/web.key.')
				])
			]);
		}

		var path = String((window.location && window.location.pathname) || ''),
		    proxyPrefix = path.replace(/\/cgi-bin\/luci(?:\/.*)?$/, '');

		if (proxyPrefix === path)
			proxyPrefix = '';

		var url = proxyPrefix + '/cgi-bin/owrt-remote?key=' + encodeURIComponent(key);

		window.setTimeout(function() {
			window.location.replace(url);
		}, 100);

		return E('div', { 'class': 'cbi-map' }, [
			E('h2', {}, _('OpenWrt Remote Hub')),
			E('p', {}, _('Opening remote access panel...')),
			E('p', {}, [
				E('a', { 'class': 'btn cbi-button cbi-button-apply', 'href': url }, _('Open panel'))
			])
		]);
	},

	handleSaveApply: null,
	handleSave: null,
	handleReset: null
});
