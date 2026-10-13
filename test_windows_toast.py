from windows_toasts import Toast, WindowsToaster

toaster = WindowsToaster("Local Mail Agent")
toast = Toast()
toast.text_fields = [
    "Mail Agent — notification test",
    "Windows desktop notifications are working."
]

toaster.show_toast(toast)
print("Toast request submitted. Check your desktop and Action Center.")
