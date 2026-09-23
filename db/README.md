# Database scripts

| Script | What | Prod-safe |
| --- | --- | --- |
| `01_performance_indexes.sql` | Drops 6 redundant indexes, adds ~35 missing ones with `CREATE INDEX CONCURRENTLY`, tunes autovacuum on the 4 big tables | Yes (online) |
| `02_jsonb_attribute.sql` | `clints.attribute json -> jsonb` (table rewrite, exclusive lock) | Maintenance window |

Local (Docker):

```
docker exec cawp-db psql -U postgres -d mydata -f /db/01_performance_indexes.sql
docker exec cawp-db psql -U postgres -d mydata -f /db/02_jsonb_attribute.sql
```

Restore the dump into the Docker DB (already done by the compose setup):

```
docker exec cawp-db pg_restore -U postgres -d mydata --no-owner --no-privileges -j 4 /dump/mydata.dump
```
| `03_last_comment.sql` | Adds `clints.last_comment_at/last_comment_id` + trigger on `"Comments"` + backfill + 3 partial indexes (neglect report / grids sort without a LATERAL) | Yes (backfill ~1-2 min, row-level trigger) |
