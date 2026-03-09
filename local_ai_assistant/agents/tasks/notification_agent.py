"""Cross-platform notification helper for the agents.tasks package.

Prefers the Windows Runtime (`winrt`) ToastNotification API when available
for native toasts, falls back to `win10toast` for simple toasts, and finally
prints to console if neither is available.

Usage: from agents.tasks.notification_agent import notify
	   notify("Title", "Message")
"""
import os
import logging

_HAS_PLYER = False
_HAS_WINRT = False
_HAS_WIN10TOAST = False

try:
	from plyer import notification as _plyer_notification
	_HAS_PLYER = True
except Exception:
	_HAS_PLYER = False

try:
	from winrt.windows.ui.notifications import ToastNotificationManager, ToastNotification
	from winrt.windows.data.xml.dom import XmlDocument
	_HAS_WINRT = True
except Exception:
	_HAS_WINRT = False

try:
	from win10toast import ToastNotifier
	_toaster = ToastNotifier()
	# win10toast's internal message-pump thread returns None from its WNDPROC
	# callback on some Windows/PyWin32 versions, which throws an uncatchable
	# "WPARAM is simple, so must be an int" TypeError in that thread.
	# Disable it here and let the console fallback handle notifications.
	_HAS_WIN10TOAST = False
except Exception:
	_HAS_WIN10TOAST = False


def _show_winrt(title: str, msg: str):
	try:
		template = f"""<toast>
  <visual>
	<binding template='ToastGeneric'>
	  <text>{title}</text>
	  <text>{msg}</text>
	</binding>
  </visual>
</toast>"""
		doc = XmlDocument()
		doc.load_xml(template)
		toast = ToastNotification(doc)
		notifier = ToastNotificationManager.create_toast_notifier()
		notifier.show(toast)
		return True
	except Exception:
		logging.exception("winrt toast failed")
		return False


def _show_win10toast(title: str, msg: str, duration: int = 5):
	try:
		# threaded=True: returns immediately so the calling thread is never blocked.
		# The toast still appears and auto-dismisses after `duration` seconds.
		_toaster.show_toast(title, msg, duration=duration, threaded=True)
		return True
	except Exception:
		logging.exception("win10toast failed")
		return False


def notify(title: str, msg: str, duration: int = 5):
	"""Show a desktop notification.

	Returns True if a native notification was attempted, False otherwise.
	"""
	# Prefer plyer (reliable cross-platform Windows notifications)
	if _HAS_PLYER:
		try:
			_plyer_notification.notify(
				title=title,
				message=msg,
				timeout=duration,
				app_name="AI Assistant",
			)
			return True
		except Exception:
			pass

	# Fallback: winrt (native Windows Notification API)
	if _HAS_WINRT:
		ok = _show_winrt(title, msg)
		if ok:
			return True

	# fallback to win10toast
	if _HAS_WIN10TOAST:
		ok = _show_win10toast(title, msg, duration=duration)
		if ok:
			return True

	# final fallback: console
	print(f"[Notification] {title}: {msg}")
	return False

