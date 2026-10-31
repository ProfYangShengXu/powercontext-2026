#!/usr/bin/env node
/**
 * 取证脚本（开发工具，不属于应用运行路径）：
 * 直接连 seekdb，导出表结构、索引、单条 HYBRID_SEARCH 的 EXPLAIN 与 GET_SQL。
 *
 * 用法：npm run evidence   （需要 seekdb 在 127.0.0.1:2881，且库里已有数据）
 */
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import mysql from 'mysql2/promise';

const here = dirname(fileURLToPath(import.meta.url));
const outDir = join(here, '..', 'docs', 'evidence');
const DB = {
  host: process.env.SEEKDB_HOST ?? '127.0.0.1',
  port: Number(process.env.SEEKDB_PORT ?? '2881'),
  user: process.env.SEEKDB_USER ?? 'root',
  password: process.env.SEEKDB_PASSWORD ?? '',
  database: process.env.SEEKDB_DATABASE ?? 'seekdb_kb',
  table: process.env.SEEKDB_TABLE ?? 'kb_chunk',
};

mkdirSync(outDir, { recursive: true });
const conn = await mysql.createConnection({ ...DB, multipleStatements: false });
const q = async (sql, params) => (await conn.query(sql, params))[0];

const show = await q(`SHOW CREATE TABLE \`${DB.table}\``);
const createSql = show[0]?.['Create Table'] ?? JSON.stringify(show);
writeFileSync(join(outDir, 'show-create-table.sql'), createSql + '\n');

const indexes = await q(`SHOW INDEX FROM \`${DB.table}\``);
writeFileSync(
  join(outDir, 'indexes.txt'),
  indexes.map((r) => `${r.Key_name}\t${r.Column_name}\t${r.Index_type}`).join('\n') + '\n',
);

const sample = await q(`SELECT id, kb_id, embedding FROM \`${DB.table}\` LIMIT 1`);
if (!sample.length) {
  console.error('表里没有数据，先导入再跑 evidence');
  process.exit(1);
}
const vector = String(sample[0].embedding);
const kbId = String(sample[0].kb_id);
const dsl = {
  query: {
    bool: {
      must: [{ match: { document: 'seekdb 混合检索' } }],
      filter: [{ term: { kb_id: kbId } }],
    },
  },
  knn: {
    field: 'embedding',
    k: 40,
    query_vector: vector,
    filter: { term: { kb_id: kbId } },
  },
  rank: { rrf: { rank_window_size: 40, rank_constant: 60 } },
  size: 10,
};
const literal = (s) => s.replace(/\\/g, '\\\\').replace(/'/g, "\\'");
const dslLiteral = literal(JSON.stringify(dsl));
const sql = `SELECT id, kb_id, document, metadata, _score FROM HYBRID_SEARCH('${DB.table}', '${dslLiteral}')`;
writeFileSync(join(outDir, 'hybrid-query.sql'), sql + '\n');

const explain = await q(`EXPLAIN ${sql}`);
const plan = explain.map((r) => r['Query Plan']).join('\n');
writeFileSync(join(outDir, 'explain.txt'), plan + '\n');

// 不带 kb 过滤的对照计划：小表上优化器可能改走 kb 索引范围扫描；
// 这个对照能证明两条臂本来就是全文索引 + 向量索引。
const plainDsl = {
  query: { match: { document: 'seekdb 混合检索' } },
  knn: { field: 'embedding', k: 40, query_vector: vector },
  rank: { rrf: { rank_window_size: 40, rank_constant: 60 } },
  size: 10,
};
const plainSql = `SELECT id, _score FROM HYBRID_SEARCH('${DB.table}', '${literal(JSON.stringify(plainDsl))}')`;
const plainExplain = await q(`EXPLAIN ${plainSql}`);
const plainPlan = plainExplain.map((r) => r['Query Plan']).join('\n');
writeFileSync(join(outDir, 'explain-no-filter.txt'), plainPlan + '\n');

const getSql = await q(`SELECT DBMS_HYBRID_SEARCH.GET_SQL('${DB.table}', '${dslLiteral}') AS s`);
writeFileSync(join(outDir, 'engine-sql.sql'), String(getSql[0]?.s ?? '') + '\n');

const result = await q(sql);
const summary = {
  generatedAt: new Date().toISOString(),
  databaseVersion: (await q('SELECT VERSION() AS v'))[0]?.v,
  table: DB.table,
  rows: (await q(`SELECT COUNT(*) AS c FROM \`${DB.table}\``))[0]?.c,
  sampleQuery: 'seekdb 混合检索',
  fusionPlanNodes: [
    'TEXT RETRIEVAL SCAN',
    'VECTOR INDEX SCAN',
    'VECTOR INDEX ADAPTIVE SCAN',
    'HASH FULL OUTER JOIN',
    'WINDOW FUNCTION',
    'TOP-N SORT',
  ].filter((k) => plan.includes(k)),
  noFilterPlanNodes: [
    'TEXT RETRIEVAL SCAN',
    'VECTOR INDEX SCAN',
    'HASH FULL OUTER JOIN',
    'WINDOW FUNCTION',
    'TOP-N SORT',
  ].filter((k) => plainPlan.includes(k)),
  topHits: result.slice(0, 5).map((r) => ({ id: r.id, kb: r.kb_id, score: r._score })),
};
writeFileSync(join(outDir, 'explain-summary.json'), JSON.stringify(summary, null, 2));
console.log(JSON.stringify(summary, null, 2));
console.log(`\n写入 ${outDir}/explain.txt, engine-sql.sql, show-create-table.sql, indexes.txt`);
await conn.end();
