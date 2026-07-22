-- Dashboard query indexes verified against the production schema on 2026-07-22.
-- Review during a maintenance window before applying to production. Index
-- creation can consume CPU, I/O and temporary disk space on large tables.

-- Supports: WHERE roomid IN (...) AND msgtime >= ... AND msgtime < ...
CREATE INDEX idx_dashboard_chat_room_msgtime
    ON qx_chat (roomid, msgtime);

-- Supports the dashboard date-range scan and keeps groupName/id in the same
-- index for latest-per-group-per-day and grouped-count queries.
CREATE INDEX idx_dashboard_analysis_time_group_id
    ON qx_analysis_result (CREATEDTIME, groupName, id);

-- Supports the nightly unanswered-analysis date scan and latest row lookup.
CREATE INDEX idx_dashboard_sale_time_group_id
    ON qx_analysis_result_sale (CREATEDTIME, groupName, id);
