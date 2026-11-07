include $(TOPDIR)/rules.mk

PKG_NAME:=luci-app-owrt-remote
PKG_VERSION:=121
PKG_RELEASE:=1
PKG_LICENSE:=MIT
PKG_MAINTAINER:=kzolotarev95
PKGARCH:=all

include $(INCLUDE_DIR)/package.mk

define Package/luci-app-owrt-remote
  PKGARCH:=all
  SECTION:=luci
  CATEGORY:=LuCI
  SUBMENU:=3. Applications
  TITLE:=OpenWrt Remote Hub router agent
  URL:=https://github.com/kzolotarev95/luci-app-owrt-remote
  DEPENDS:=+luci-base +rpcd-mod-file +uhttpd +jsonfilter +curl +ca-bundle +etherwake
endef

define Package/luci-app-owrt-remote/description
  Router agent and LuCI panel for OpenWrt Remote Hub. Includes Heartbeat,
  independent remote Xray control, WAN direct and bounded persistent logs.
  Xray is selected and verified for the router by the existing agent when
  applying the Hub configuration; it is not bundled into this noarch package.
endef

define Package/luci-app-owrt-remote/conffiles
/etc/config/owrtremote
endef

# scripts/openwrt_package.py prepare creates this self-contained SDK package
# from files/ and root/. There are no separately maintained runtime copies.
define Build/Prepare
	mkdir -p $(PKG_BUILD_DIR)
endef

define Build/Configure
endef

define Build/Compile
endef

define Package/luci-app-owrt-remote/install
	$(CP) ./root/. $(1)/
endef

define Package/luci-app-owrt-remote/postinst
#!/bin/sh
[ -n "$${IPKG_INSTROOT}" ] || /bin/sh /usr/lib/owrt-remote/package.sh configure
endef

define Package/luci-app-owrt-remote/postrm
#!/bin/sh
[ -n "$${IPKG_INSTROOT}" ] || { \
	rm -f /tmp/luci-indexcache /tmp/luci-indexcache.*; \
	rm -rf /tmp/luci-modulecache; \
	[ ! -x /etc/init.d/rpcd ] || /etc/init.d/rpcd reload; \
}
exit 0
endef

# OpenWrt's generated postinst/prerm handle enable/start and stop/disable for
# both procd services, and PKG_UPGRADE keeps upgrade semantics on IPK and APK.
# Do not duplicate them or depend on opkg's "configure" argument: apk invokes
# post-install/post-upgrade with version arguments instead.
$(eval $(call BuildPackage,luci-app-owrt-remote))
