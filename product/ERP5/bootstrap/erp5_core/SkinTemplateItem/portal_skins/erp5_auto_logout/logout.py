from AccessControl import getSecurityManager
portal = context.getPortalObject()
username = getSecurityManager().getUser().getId()
if username is not None:
  portal.portal_sessions.manage_delObjects(
    portal.Base_getAutoLogoutSessionKey(
      username=username,
    )
  )
REQUEST = portal.REQUEST
if REQUEST.has_key('portal_skin'):
  portal.portal_skins.clearSkinCookie()
REQUEST.RESPONSE.expireCookie('__ac', path='/')
return REQUEST.RESPONSE.redirect(REQUEST.URL1 + '/logged_out')
