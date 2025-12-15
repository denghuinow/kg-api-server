@kg-api-server 实现get_full_data和get_incremental_data，通过数据库查询数据。
数据库定义如下：
```
CREATE TABLE "public"."knowledge_chunks_kg_test" (
  "id" uuid NOT NULL DEFAULT gen_random_uuid(),
  "content" text COLLATE "pg_catalog"."default" NOT NULL,
  "created_at" timestamp(6) NOT NULL DEFAULT now(),
  "is_delete" bool NOT NULL DEFAULT false,
  "embedding" text COLLATE "pg_catalog"."default",
  "source_id" uuid,
  CONSTRAINT "knowledge_chunks_copy1_pkey" PRIMARY KEY ("id")
)
;

ALTER TABLE "public"."knowledge_chunks_kg_test" 
  OWNER TO "vector_user";
```


数据集连接信息：
```
postgresql://vector_user:vector_pass@172.16.15.236:5432/data_integration
```

要求表名可配置，但不需要创建表。