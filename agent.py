def run_task(operation):
    print("Task started")

    try:
        result = operation()
        print("Task completed")
        return result

    except Exception as error:
        print(f"Task failed: {error}")
        raise
