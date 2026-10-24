"""工作台数字分页的共同数据边界；不影响未请求页码的游标API。"""

from sqlalchemy import func, select

from app.common.errors import require
from app.common.pagination import bounded_stream_page

MAX_NUMBERED_OFFSET = 100000


async def numbered_rows(db, query, page, limit, *, scalars=True):
    # READ COMMITTED 下统计和列表各有语句快照，总数是即时导航参考。
    # 不为UI页码改变整个应用的隔离级别；空页范围必须以实际返回行为准。
    total = await db.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    pages = max(1, (total + limit - 1) // limit)
    current = min(page, pages)
    offset = (current - 1) * limit
    require(
        offset <= MAX_NUMBERED_OFFSET,
        422,
        "invalid_pagination",
        "页码过深，请先缩小筛选范围，或使用游标接口继续读取",
    )
    result = await db.execute(query.offset(offset).limit(limit))
    rows = result.scalars().all() if scalars else result.all()
    return rows, {
        "page": current,
        "page_size": limit,
        "total": total,
        "pages": pages,
        "start": offset + 1 if rows else 0,
        "end": offset + len(rows) if rows else 0,
    }


async def stream_page(db, query, view, key, limit):
    # 动态字段行可能较大；每次只预取一行，异常、取消与提前达到预算均关闭游标。
    stream = await db.stream_scalars(query.limit(limit + 1).execution_options(yield_per=1))
    try:
        return await bounded_stream_page(stream, view, key, limit)
    finally:
        await stream.close()
