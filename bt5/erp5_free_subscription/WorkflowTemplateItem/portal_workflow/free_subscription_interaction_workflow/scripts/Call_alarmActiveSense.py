document = state_change['object']
alarm = document.getPortalObject().portal_alarms.accept_submitted_free_subscription_requests
if alarm.isEnabled():
  tag = document.getRelativeUrl() + '_reindex'
  document.reindexObject(activate_kw={'tag': tag})
  alarm.activate(activity='SQLQueue', after_tag=tag).activeSense()
