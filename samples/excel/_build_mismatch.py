"""Generate users_with_extras.xlsx + users_minimal.xlsx for field-picker demos.
Run from inside the container or any env with openpyxl installed."""
from openpyxl import Workbook
from pathlib import Path

OUT = Path(__file__).parent

src_headers = ['序号', 'id', 'name', 'email', 'dept_id', 'salary', 'status', '备注']
src_rows = [
    [1, 1, '张三', 'zhangsan@a.com', 10, 15000, 'active', '初次入职'],
    [2, 2, '李四', 'lisi@a.com',     10, 16000, 'active', ''],
    [3, 3, '王五', 'wangwu@a.com',   20, 17000, 'active', ''],
    [4, 4, '赵六', 'zhaoliu@a.com',  20, 18000, 'inactive', '已离职'],
    [5, 5, '孙七', 'sunqi@a.com',    30, 19000, 'active', ''],
    [6, 6, '周八', 'zhouba@a.com',   30, 20000, 'active', ''],
]

tgt_headers = ['id', 'name', 'email', 'dept_id', 'salary', 'role']
tgt_rows = [
    [1, '张三', 'zhangsan@a.com', 10, 15000, 'engineer'],
    [2, '李四', 'lisi@a.com',     10, 14500, 'engineer'],
    [3, '王五·新', 'wangwu@a.com', 20, 17000, 'engineer'],
    [5, '孙七', 'sunqi@a.com',    30, 19000, 'manager'],
    [6, '周八', 'zhouba@a.com',   30, 20000, 'engineer'],
    [7, '吴九', 'wujiu@a.com',    40, 21000, 'engineer'],
]


def build(path: Path, sheet_name: str, headers, rows):
    wb = Workbook()
    ws = wb.active
    ws.title = sheet_name
    ws.append(headers)
    for row in rows:
        ws.append(row)
    wb.save(path)


if __name__ == "__main__":
    build(OUT / 'users_with_extras.xlsx', 'users', src_headers, src_rows)
    build(OUT / 'users_minimal.xlsx', 'users', tgt_headers, tgt_rows)
    print('源列:', src_headers)
    print('目标列:', tgt_headers)
    print('双:', sorted(set(src_headers) & set(tgt_headers)))
    print('仅源:', sorted(set(src_headers) - set(tgt_headers)))
    print('仅目标:', sorted(set(tgt_headers) - set(src_headers)))
