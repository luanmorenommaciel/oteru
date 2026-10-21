ATTACH TABLE _ UUID '0e9781c0-cfcb-4f3f-8c37-2d0b42743cd2'
(
    `test` String
)
ENGINE = MergeTree
ORDER BY tuple()
SETTINGS index_granularity = 8192
