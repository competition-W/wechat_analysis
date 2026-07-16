-- LIMS 最终售后对应表：先执行本文件，再发布包含维护接口的应用版本。
-- 可重复执行；首版 1900-01 作为全历史基线。

CREATE TABLE IF NOT EXISTS dashboard_aftersaler_mapping_version (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    effective_month DATE NOT NULL,
    revision INT UNSIGNED NOT NULL DEFAULT 1,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_dashboard_aftersaler_mapping_month (effective_month)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS dashboard_aftersaler_mapping_rule (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    version_id BIGINT UNSIGNED NOT NULL,
    product_name VARCHAR(100) NOT NULL,
    product_keywords JSON NOT NULL,
    region_name VARCHAR(100) NOT NULL,
    lims_aftersaler VARCHAR(100) NOT NULL,
    actual_aftersaler VARCHAR(100) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    deleted_at TIMESTAMP NULL DEFAULT NULL,
    PRIMARY KEY (id),
    KEY idx_dashboard_aftersaler_rule_version (version_id, deleted_at),
    KEY idx_dashboard_aftersaler_rule_lookup (version_id, region_name, lims_aftersaler),
    CONSTRAINT fk_dashboard_aftersaler_rule_version
        FOREIGN KEY (version_id) REFERENCES dashboard_aftersaler_mapping_version(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS dashboard_aftersaler_mapping_audit (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    version_id BIGINT UNSIGNED NOT NULL,
    rule_id BIGINT UNSIGNED NULL,
    action VARCHAR(30) NOT NULL,
    before_json JSON NULL,
    after_json JSON NULL,
    actor_hash VARCHAR(32) NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    KEY idx_dashboard_aftersaler_audit_version (version_id, created_at),
    CONSTRAINT fk_dashboard_aftersaler_audit_version
        FOREIGN KEY (version_id) REFERENCES dashboard_aftersaler_mapping_version(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO dashboard_aftersaler_mapping_version (effective_month, revision)
VALUES ('1900-01-01', 1)
ON DUPLICATE KEY UPDATE effective_month = VALUES(effective_month);

INSERT INTO dashboard_aftersaler_mapping_rule
    (version_id, product_name, product_keywords, region_name, lims_aftersaler, actual_aftersaler)
SELECT v.id, seed.product_name, JSON_ARRAY(seed.keyword), seed.region_name,
       seed.lims_aftersaler, seed.actual_aftersaler
FROM dashboard_aftersaler_mapping_version v
JOIN (
    SELECT 'ATAC' product_name, 'ATAC' keyword, '北京区' region_name, '杨嘉俊' lims_aftersaler, '吴志浩' actual_aftersaler
    UNION ALL SELECT 'ATAC','ATAC','华东一区','杨嘉俊','吴志浩'
    UNION ALL SELECT 'ATAC','ATAC','华东三区','杨嘉俊','吴志浩'
    UNION ALL SELECT 'ATAC','ATAC','华东二区','杨嘉俊','吴志浩'
    UNION ALL SELECT 'ATAC','ATAC','华南区','杨嘉俊','吴志浩'
    UNION ALL SELECT 'ChIP','ChIP','北京区','来智健','来智健'
    UNION ALL SELECT 'ChIP','ChIP','东北区','来智健','来智健'
    UNION ALL SELECT 'ChIP','ChIP','西北西南区','来智健','来智健'
    UNION ALL SELECT 'm6A','m6A','川渝区','来智健','杨嘉俊'
    UNION ALL SELECT 'm6A','m6A','东北区','来智健','杨嘉俊'
    UNION ALL SELECT 'm6A','m6A','华东二区','来智健','杨嘉俊'
    UNION ALL SELECT 'm6A','m6A','华东三区','来智健','杨嘉俊'
    UNION ALL SELECT 'm6A','m6A','华东一区','来智健','杨嘉俊'
    UNION ALL SELECT 'm6A','m6A','华中区','来智健','杨嘉俊'
    UNION ALL SELECT 'm6A','m6A','西北西南区','来智健','杨嘉俊'
    UNION ALL SELECT 'm6A','m6A','美国','来智健','杨嘉俊'
    UNION ALL SELECT 'RIP','RIP','全国','来智健','来智健'
    UNION ALL SELECT 'ChIP','ChIP','川渝区','杨嘉俊','来智健'
    UNION ALL SELECT 'ChIP','ChIP','华东一区','杨嘉俊','来智健'
    UNION ALL SELECT 'ChIP','ChIP','华东二区','杨嘉俊','来智健'
    UNION ALL SELECT 'ChIP','ChIP','华东三区','杨嘉俊','来智健'
    UNION ALL SELECT 'CUT&Tag','CUT&Tag','北京区','杨嘉俊','杨嘉俊'
    UNION ALL SELECT 'CUT&Tag','CUT&Tag','华东一区','杨嘉俊','杨嘉俊'
    UNION ALL SELECT 'CUT&Tag','CUT&Tag','华东二区','杨嘉俊','杨嘉俊'
    UNION ALL SELECT 'CUT&Tag','CUT&Tag','华东三区','杨嘉俊','杨嘉俊'
    UNION ALL SELECT 'CUT&Tag','CUT&Tag','华中区','杨嘉俊','杨嘉俊'
    UNION ALL SELECT 'm6A','m6A','北京区','杨嘉俊','杨嘉俊'
    UNION ALL SELECT 'm6A','m6A','华南区','杨嘉俊','杨嘉俊'
    UNION ALL SELECT 'ChIP','ChIP','华南区','李春雨','吴志浩'
    UNION ALL SELECT 'ChIP','ChIP','华中区','李春雨','吴志浩'
    UNION ALL SELECT 'CUT&Tag','CUT&Tag','川渝区','李春雨','吴志浩'
    UNION ALL SELECT 'CUT&Tag','CUT&Tag','东北区','李春雨','吴志浩'
    UNION ALL SELECT 'CUT&Tag','CUT&Tag','华南区','李春雨','吴志浩'
    UNION ALL SELECT 'CUT&Tag','CUT&Tag','西北西南区','李春雨','吴志浩'
    UNION ALL SELECT 'DAP / 翻译组（遗留项目）','DAP','全国','李春雨','吴志浩'
    UNION ALL SELECT 'ATAC','ATAC','华中区','李春雨','刘安民'
    UNION ALL SELECT 'ATAC','ATAC','西北西南区','李春雨','刘安民'
    UNION ALL SELECT 'ATAC','ATAC','东北区','李春雨','刘安民'
    UNION ALL SELECT 'ATAC','ATAC','川渝区','李春雨','刘安民'
    UNION ALL SELECT 'lncRNA','lncRNA','华中区','李春雨','李春雨'
    UNION ALL SELECT 'lncRNA','lncRNA','川渝区','李春雨','李春雨'
    UNION ALL SELECT 'lncRNA','lncRNA','西北西南区','李春雨','李春雨'
    UNION ALL SELECT 'lncRNA','lncRNA','东北区','李春雨','李春雨'
    UNION ALL SELECT 'lncRNA','lncRNA','北京区','李春雨','李春雨'
    UNION ALL SELECT 'lncRNA','lncRNA','华东一区','李春雨','李春雨'
    UNION ALL SELECT 'lncRNA','lncRNA','华东二区','李春雨','李春雨'
    UNION ALL SELECT 'lncRNA','lncRNA','华东三区','李春雨','李春雨'
    UNION ALL SELECT 'lncRNA','lncRNA','华南区','李春雨','李春雨'
    UNION ALL SELECT '降解组','降解','华中区','李春雨','李春雨'
    UNION ALL SELECT '降解组','降解','川渝区','李春雨','李春雨'
    UNION ALL SELECT '降解组','降解','西北西南区','李春雨','李春雨'
    UNION ALL SELECT '降解组','降解','东北区','李春雨','李春雨'
    UNION ALL SELECT '降解组','降解','北京区','李春雨','李春雨'
    UNION ALL SELECT '降解组','降解','华东一区','李春雨','李春雨'
    UNION ALL SELECT '降解组','降解','华东二区','李春雨','李春雨'
    UNION ALL SELECT '降解组','降解','华东三区','李春雨','李春雨'
    UNION ALL SELECT '降解组','降解','华南区','李春雨','李春雨'
    UNION ALL SELECT 'DNA甲基化','DNA甲基化','华中区','来智健','来智健'
    UNION ALL SELECT 'DNA甲基化','DNA甲基化','川渝区','来智健','来智健'
    UNION ALL SELECT 'DNA甲基化','DNA甲基化','西北西南区','来智健','来智健'
    UNION ALL SELECT 'DNA甲基化','DNA甲基化','东北区','来智健','刘安民'
    UNION ALL SELECT 'DNA甲基化','DNA甲基化','北京区','来智健','刘安民'
    UNION ALL SELECT 'DNA甲基化','DNA甲基化','华东一区','来智健','刘安民'
    UNION ALL SELECT 'DNA甲基化','DNA甲基化','华东二区','来智健','刘安民'
    UNION ALL SELECT 'DNA甲基化','DNA甲基化','华东三区','来智健','刘安民'
    UNION ALL SELECT 'DNA甲基化','DNA甲基化','华南区','来智健','刘安民'
    UNION ALL SELECT 'TCR_BCR','TCR_BCR','华中区','来智健','刘安民'
    UNION ALL SELECT 'TCR_BCR','TCR_BCR','川渝区','来智健','刘安民'
    UNION ALL SELECT 'TCR_BCR','TCR_BCR','西北西南区','来智健','刘安民'
    UNION ALL SELECT 'TCR_BCR','TCR_BCR','东北区','来智健','刘安民'
    UNION ALL SELECT 'TCR_BCR','TCR_BCR','北京区','来智健','刘安民'
    UNION ALL SELECT 'TCR_BCR','TCR_BCR','华东一区','来智健','刘安民'
    UNION ALL SELECT 'TCR_BCR','TCR_BCR','华东二区','来智健','刘安民'
    UNION ALL SELECT 'TCR_BCR','TCR_BCR','华东三区','来智健','刘安民'
    UNION ALL SELECT 'TCR_BCR','TCR_BCR','华南区','来智健','刘安民'
    UNION ALL SELECT 'miRNA','miRNA','华中区','潘和娟','潘和娟'
    UNION ALL SELECT 'miRNA','miRNA','川渝区','潘和娟','潘和娟'
    UNION ALL SELECT 'miRNA','miRNA','西北西南区','潘和娟','潘和娟'
    UNION ALL SELECT 'miRNA','miRNA','东北区','潘和娟','潘和娟'
    UNION ALL SELECT 'miRNA','miRNA','北京区','潘和娟','潘和娟'
    UNION ALL SELECT 'miRNA','miRNA','华东一区','姚秀华','姚秀华'
    UNION ALL SELECT 'miRNA','miRNA','华东二区','姚秀华','姚秀华'
    UNION ALL SELECT 'miRNA','miRNA','华东三区','姚秀华','姚秀华'
    UNION ALL SELECT 'miRNA','miRNA','华南区','姚秀华','姚秀华'
) seed ON 1=1
WHERE v.effective_month = '1900-01-01'
  AND NOT EXISTS (
      SELECT 1 FROM dashboard_aftersaler_mapping_rule existing
      WHERE existing.version_id = v.id
        AND existing.product_name = seed.product_name
        AND existing.region_name = seed.region_name
        AND existing.lims_aftersaler = seed.lims_aftersaler
  );
