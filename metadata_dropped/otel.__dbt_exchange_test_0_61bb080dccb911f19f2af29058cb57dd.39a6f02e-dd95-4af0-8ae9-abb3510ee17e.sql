ATTACH TABLE _ UUID '39a6f02e-dd95-4af0-8ae9-abb3510ee17e'
(
    `test` String
)
ENGINE = MergeTree
ORDER BY tuple()
SETTINGS index_granularity = 8192
