# MySQL 备份与恢复（ztb_demo）

> 结论先行：**不跑每日定时备份**，改为「一份异地冷备份 + 可验证的恢复步骤」。
> 前提是「数据静态」——这个前提一旦失效，本文的结论就失效（见 §4）。

---

## 1. 为什么不做每日备份

不是省事，是因为**备份防的是「变」，而这里的东西不变**：

| 检查 | 结论 |
|---|---|
| 业务代码对 MySQL 的写操作 | `core/` `api/` `price_engine/` 里 `INSERT INTO` / `UPDATE` / `REPLACE INTO` / `DELETE FROM` **0 命中** → 业务侧**只读** |
| 飞书 open_id 绑定 | 存 memory / redis（`core/feishu_binding.py`），**不落 MySQL** |
| 会话 / checkpoint | checkpointer（MemorySaver / sqlite），不在 MySQL |
| 上传文件 | filestore（本地卷 / redis），不在 MySQL |
| 数据来源 | 一次性导入（`delivery_package` 重建 或 dump 导入），之后不再写 |

所以每天 dump 一份和上一天**逐字节相同**的东西，只是浪费 I/O。

剩下的真实风险是「**丢**」，不是「变」：

- 误 `DROP DATABASE` / 误删 `mysql_data` 目录
- 服务器单盘（`/dev/vda2`，**无冗余**）故障
- 服务器到期 / 欠费被回收 / 重装系统

这四种情况，一份异地冷备份就够覆盖。

> 顺带澄清一个常见误解：MySQL 的 binlog 是开着的（`log_bin=1, ROW`），能做时间点恢复，
> 但 **binlog 就在同一个 `mysql_data` 目录、同一块盘上** —— 盘没了它一起没。binlog ≠ 备份。

---

## 2. 数据在哪

| 形态 | 位置 | 说明 |
|---|---|---|
| **在线数据** | 服务器 `/root/ztb_demo/docker/mysql/mysql_data`（bind mount → `/var/lib/mysql`） | 约 1.2 GB（含 binlog/索引），在系统盘上 |
| **异地冷备份** | `D:\backup\ztb\ztb_mysql_20261128.sql.gz` | **24.3 MB**，MD5 `d0b6d3114f72cd95a1962fe243830140` |
| 原始素材 | 本仓库 `delivery_package/`（11 个 jsonl + `mysql_schema.sql` + `reference_loader.py`） | 可完全重建 `bidding` 库 |

冷备份内容：`bidding`（15 表）+ `ztb_clean`（4 表），`mysqldump --single-transaction --routines --triggers --databases`。

---

## 3. 恢复步骤（约 2 分钟）

### 3.1 拿口令

```bash
# 口令只存在于容器环境与 /root/ztb_demo/.env（600），本文不记录明文
DBPW=$(docker inspect ztb_mysql --format '{{range .Config.Env}}{{println .}}{{end}}' \
        | grep '^MYSQL_ROOT_PASSWORD=' | cut -d= -f2-)
```

### 3.2 传备份上去并导入

```bash
# 本机（Windows）
scp D:\backup\ztb\ztb_mysql_20261128.sql.gz root@120.48.149.138:/tmp/ztb_restore.sql.gz
```

```bash
# 服务器
gunzip -c /tmp/ztb_restore.sql.gz | docker exec -i -e MYSQL_PWD="$DBPW" ztb_mysql mysql -uroot
```

`mysqldump --databases` 的产物含 `CREATE DATABASE IF NOT EXISTS` 与逐表 `DROP TABLE IF EXISTS`，
**可以重复执行**，不会因为表已存在而失败。

### 3.3 校验

```bash
docker exec -e MYSQL_PWD="$DBPW" ztb_mysql mysql -uroot -t -e "
  SELECT COUNT(*) AS company_info FROM ztb_clean.company_info;      -- 期望 38911
  SELECT COUNT(*) AS bid_project  FROM ztb_clean.bid_project;       -- 期望 17742
  SELECT COUNT(*) AS penalty      FROM ztb_clean.company_penalty;   -- 期望 1805
  SELECT COUNT(*) AS project      FROM bidding.project_unified;     -- 期望 17639
"
```

> ⚠️ 别用 `information_schema.table_rows` 判断 —— InnoDB 的统计是**估算值且可能滞后**，
> 刚导完会显示 0。今天就是被这个数骗过一次，`COUNT(*)` 才是真的。

接口层再确认一次：

```bash
curl -s -o /dev/null -w '%{http_code}\n' https://ztb.openagent-lab.top/api/kanban/overview   # 200
```

### 3.4 完全没有备份时的兜底

用 `delivery_package` 从 jsonl 重建（`bidding` 库）：

```bash
docker exec -i -e MYSQL_PWD="$DBPW" ztb_mysql mysql -uroot < delivery_package/mysql_schema.sql
DB_HOST=127.0.0.1 DB_USER=root DB_PASSWORD=... DB_NAME=bidding \
  python delivery_package/reference_loader.py --dir delivery_package
```

⚠️ 这条路径只能重建 `bidding` 的 11 张基础表，**重建不出** `project_unified` / `company_unified`
这些派生宽表，也覆盖不了 `ztb_clean` —— 所以它是兜底，不是等价方案。

---

## 4. 什么时候必须重启「每日备份」

出现以下任一情况，本文结论作废，请改回定时备份（`mysqldump` + 保留 7 天 + 至少一份离机）：

1. 业务代码开始写 MySQL（新增写入路径 / 出现用户数据落库）
2. 重新跑爬虫或清洗，数据开始**增量更新**
3. 多租户 / 多用户写入（配额、审计、订单）
4. 数据量或业务重要性上升到「丢了要重跑几天」

判断方法很简单：**重新 grep 一次写操作**，有命中就重启备份。

```bash
grep -rnE 'INSERT INTO|REPLACE INTO|DELETE FROM|UPDATE `' --include=*.py core api price_engine
```

---

## 5. 记住两件事

- **冷备份要定期验活**：`.gz` 能解开、前几行是 `-- MySQL dump` 就算活着（本文写作时已验证）。
- **改完 `.env` 要 `--force-recreate`**：`docker restart` / 普通 `up -d` 都不会重读环境变量。
