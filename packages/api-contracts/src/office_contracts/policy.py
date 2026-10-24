"""恢复建议不授权自动重试；不凭HTTP码判断写入未发生。"""

from .models import Recovery


def transport_recovery(*, writing: bool, definitely_not_sent: bool = False) -> Recovery:
    return Recovery(
        action="query_operation" if writing else "retry_later",
        write_outcome=("not_applied" if definitely_not_sent else "unknown")
        if writing
        else "not_applicable",
    )
