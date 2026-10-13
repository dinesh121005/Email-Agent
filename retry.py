import time


def retry_call(operation, max_attempts=3, delay_seconds=1):
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")

    for attempt in range(1, max_attempts + 1):
        try:
            return operation()

        except TimeoutError:
            if attempt == max_attempts:
                raise

            time.sleep(delay_seconds * (2 ** (attempt - 1)))
