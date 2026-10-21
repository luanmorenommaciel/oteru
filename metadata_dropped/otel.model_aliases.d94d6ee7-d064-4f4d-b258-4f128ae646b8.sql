ATTACH TABLE _ UUID 'd94d6ee7-d064-4f4d-b258-4f128ae646b8'
(
    `model_raw` String,
    `model_family` String
)
ENGINE = MergeTree
ORDER BY (tuple())
SETTINGS index_granularity = 8192
