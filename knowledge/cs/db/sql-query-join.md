# SQL 查询与多表连接

## 一句话结论

一条 SELECT 的书写顺序是 SELECT-FROM-WHERE-GROUP BY-HAVING-ORDER BY-LIMIT，但**执行顺序**不同（先 FROM/JOIN 取表、再 WHERE 过滤、再分组聚合、最后排序截断）；多表连接里 INNER JOIN 只留两边都匹配的行，LEFT JOIN 保留左表全部行、右表补 NULL，搞不清这个就会在 WHERE 里把外连接「写回」内连接。

## 标准答案

**SELECT 的执行顺序**

1. FROM / JOIN：确定数据来源，生成笛卡尔积再按 ON 条件筛。
2. WHERE：对连接后的行做行级过滤（此时不能用聚合结果）。
3. GROUP BY：分组。
4. HAVING：对分组后的结果过滤（可以用 COUNT/SUM 等聚合值）。
5. SELECT：挑列、算表达式。
6. DISTINCT：去重（在 SELECT 之后）。
7. ORDER BY：排序（可以用 SELECT 里起的别名）。
8. LIMIT / OFFSET：截断分页。

所以「WHERE 里不能写聚合条件」是语法层面的必然——WHERE 执行时还没分组，聚合值尚不存在；要筛聚合结果必须写进 HAVING。

**四种 JOIN 的差别**

- INNER JOIN：只返回左右都能匹配上的行。
- LEFT JOIN：左表行全部保留，右表没匹配到的列填 NULL；行数 ≥ 左表行数（右表一对多时会膨胀）。
- RIGHT JOIN：反之，实际很少用（改写方向即可）。
- FULL OUTER JOIN：两边都保留；MySQL 不支持，需用 LEFT JOIN UNION RIGHT JOIN 模拟。

**ON 和 WHERE 在外连接里含义不同**

ON 是「连接时的匹配条件」，决定右表哪一行被配上；WHERE 是「连接完成后对结果行的过滤」。在 LEFT JOIN 中把右表的条件写进 WHERE（如 `WHERE b.status = 1`），右表补 NULL 的行会被 `NULL = 1` 判为假而整行丢弃，效果退化成 INNER JOIN。要保留左表全量，条件必须写在 ON 里：`LEFT JOIN b ON b.a_id = a.id AND b.status = 1`。

## 原理展开

**为什么 JOIN 前要先看驱动表**

嵌套循环连接（NLJ）是拿驱动表的每一行去被驱动表匹配，被驱动表上若有索引，每次匹配就从 O(N) 降为 O(log N)。所以「小表驱动大表 + 被驱动表连接列建索引」是最基本的一条优化；优化器一般会自己选，但统计信息不准时会选错，可用 EXPLAIN 查看。

**COUNT(*) 与 COUNT(列) 的区别**

COUNT(*) 统计行数（含全 NULL 行）；COUNT(列) 只统计该列非 NULL 的行数；COUNT(1) 与 COUNT(*) 在现代优化器里通常等价。另外 MyISAM 会缓存整表行数所以 COUNT(*) 很快，InnoDB 因 MVCC 需逐行判断可见性，大表全表 COUNT 会慢。

**GROUP BY 与 DISTINCT 的关系**

两者都会触发分组/去重，GROUP BY 可以带聚合，DISTINCT 只是去重。对同一列 `GROUP BY col` 与 `SELECT DISTINCT col` 结果集等价，但 GROUP BY 在 MySQL 5.7 及以前会隐式排序（8.0 起不再保证），不要依赖顺序，要排序就显式写 ORDER BY。

## 面试常问

1. WHERE 和 HAVING 的区别？WHERE 在分组前对行过滤、不能含聚合函数；HAVING 在分组后对组过滤、可以用聚合值。
2. LEFT JOIN 和 INNER JOIN 结果差在哪？LEFT JOIN 保留左表中右表匹配不上的行（补 NULL），INNER JOIN 不保留；把右表条件误写进 WHERE 会让 LEFT JOIN 退化成 INNER JOIN。
3. 大表分页怎么优化？`LIMIT 1000000, 20` 要先扫描并丢弃前一百万行，改成「上一页最大 id + WHERE id > ?」的游标式分页，或用覆盖索引先取主键再回表。
4. IN 和 EXISTS 怎么选？主表小、子表大且子表连接列有索引时用 IN；主表大、子表小时用 EXISTS（先遍历主表再探测）。现代优化器多会改写，但语义上 IN 会先算子查询并去重、EXISTS 是布尔半连接。
