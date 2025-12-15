#!/usr/bin/env python3
"""
独立的测试脚本：将文本文件分块后存入数据库表

用法示例:
    python fill_test_data.py \
        --file /path/to/text.txt \
        --db-url "postgresql://user:pass@host:port/database" \
        --table "knowledge_chunks_kg_test"
"""
import argparse
import sys
import uuid
from pathlib import Path
from typing import List, Optional

import psycopg2
import requests
from psycopg2 import sql


def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description="将文本文件分块后存入数据库表",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 基本用法
  python fill_test_data.py \\
      --file /path/to/text.txt \\
      --db-url "postgresql://vector_user:vector_pass@172.16.15.236:5432/data_integration" \\
      --table "knowledge_chunks_kg_test"

  # 指定 source_id
  python fill_test_data.py \\
      --file /path/to/text.txt \\
      --db-url "postgresql://vector_user:vector_pass@172.16.15.236:5432/data_integration" \\
      --table "knowledge_chunks_kg_test" \\
      --source-id "550e8400-e29b-41d4-a716-446655440000"

  # 自定义分块参数
  python fill_test_data.py \\
      --file /path/to/text.txt \\
      --db-url "postgresql://vector_user:vector_pass@172.16.15.236:5432/data_integration" \\
      --table "knowledge_chunks_kg_test" \\
      --api-url "http://localhost:8011/SentenceChunker" \\
      --chunk-size 1024 \\
      --chunk-overlap 50
        """,
    )

    parser.add_argument(
        "--file",
        "-f",
        type=str,
        required=True,
        help="文本文件路径",
    )

    parser.add_argument(
        "--db-url",
        "-d",
        type=str,
        required=True,
        help="数据库连接字符串，格式: postgresql://user:pass@host:port/database",
    )

    parser.add_argument(
        "--table",
        "-t",
        type=str,
        default="knowledge_chunks_kg_test",
        help="表名（默认: knowledge_chunks_kg_test）",
    )

    parser.add_argument(
        "--api-url",
        "-a",
        type=str,
        default="http://172.16.15.249:8011/SentenceChunker",
        help="chonkie-fastapi API URL（默认: http://172.16.15.249:8011/SentenceChunker）",
    )

    parser.add_argument(
        "--chunk-size",
        type=int,
        default=512,
        help="分块大小（默认: 512）",
    )

    parser.add_argument(
        "--chunk-overlap",
        type=int,
        default=20,
        help="分块重叠（默认: 20）",
    )

    parser.add_argument(
        "--source-id",
        type=str,
        default=None,
        help="source_id 字段值（UUID 格式，可选）",
    )

    return parser.parse_args()


def read_text_file(file_path: str) -> str:
    """读取文本文件内容"""
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"文件不存在: {file_path}")

    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        if not content.strip():
            raise ValueError(f"文件为空: {file_path}")
        return content
    except UnicodeDecodeError as e:
        raise ValueError(f"文件编码错误，无法以 UTF-8 读取: {file_path}") from e


def chunk_text(text: str, api_url: str, chunk_size: int, chunk_overlap: int) -> List[str]:
    """调用 chonkie-fastapi API 进行文本分块"""
    payload = {
        "text": text,
        "chunk_size": chunk_size,
        "chunk_overlap": chunk_overlap,
        "min_sentences_per_chunk": 1,
        "min_characters_per_sentence": 12,
        "approximate": True,
        "delim": [".", "?", "!", "。", "？", "！", "\n"],
        "include_delim": "prev",
        "return_type": "chunks",
    }

    try:
        print(f"调用 API: {api_url}")
        print(f"  文本长度: {len(text)} 字符")
        print(f"  分块参数: chunk_size={chunk_size}, chunk_overlap={chunk_overlap}")

        response = requests.post(api_url, json=payload, timeout=300)
        response.raise_for_status()

        result = response.json()
        chunks_data = result.get("chunks", [])

        if not chunks_data:
            raise ValueError("API 返回的分块列表为空")

        chunks = [chunk["text"] for chunk in chunks_data if chunk.get("text")]
        print(f"  分块完成: {len(chunks)} 个分块")
        return chunks

    except requests.exceptions.Timeout:
        raise RuntimeError(f"API 请求超时: {api_url}")
    except requests.exceptions.ConnectionError:
        raise RuntimeError(f"无法连接到 API 服务: {api_url}。请确认服务正在运行")
    except requests.exceptions.HTTPError as e:
        error_detail = ""
        if hasattr(e.response, "text"):
            error_detail = f": {e.response.text[:500]}"
        raise RuntimeError(f"API 请求失败 (HTTP {e.response.status_code}){error_detail}") from e
    except requests.exceptions.RequestException as e:
        raise RuntimeError(f"API 请求异常: {e}") from e
    except (KeyError, TypeError, ValueError) as e:
        raise RuntimeError(f"解析 API 响应失败: {e}") from e


def insert_chunks_to_db(
    db_url: str,
    table_name: str,
    chunks: List[str],
    source_id: Optional[str] = None,
) -> int:
    """将分块数据插入数据库"""
    if not chunks:
        raise ValueError("分块列表为空，无法插入")

    # 验证 source_id 格式（如果提供）
    source_id_uuid = None
    if source_id:
        try:
            source_id_uuid = uuid.UUID(source_id)
        except ValueError:
            raise ValueError(f"无效的 source_id 格式: {source_id}。必须是有效的 UUID")

    conn = None
    try:
        print(f"连接数据库...")
        conn = psycopg2.connect(db_url)

        with conn.cursor() as cur:
            # 使用参数化查询插入数据
            insert_query = sql.SQL(
                "INSERT INTO {} (content, is_delete, embedding, source_id) VALUES (%s, %s, %s, %s)"
            ).format(sql.Identifier(table_name))

            # 准备批量插入数据
            insert_data = [
                (chunk, False, None, source_id_uuid) for chunk in chunks
            ]

            print(f"插入 {len(chunks)} 条记录到表 {table_name}...")
            cur.executemany(insert_query, insert_data)
            conn.commit()

            inserted_count = cur.rowcount
            print(f"成功插入 {inserted_count} 条记录")
            return inserted_count

    except psycopg2.OperationalError as e:
        raise RuntimeError(f"数据库连接失败: {e}") from e
    except psycopg2.ProgrammingError as e:
        raise RuntimeError(f"SQL 执行错误: {e}") from e
    except psycopg2.Error as e:
        if conn:
            conn.rollback()
        raise RuntimeError(f"数据库错误: {e}") from e
    finally:
        if conn:
            conn.close()


def main():
    """主函数"""
    try:
        args = parse_args()

        print("=" * 60)
        print("文本文件分块并存入数据库")
        print("=" * 60)

        # 读取文件
        print(f"\n[1/3] 读取文件: {args.file}")
        text = read_text_file(args.file)
        print(f"  文件大小: {len(text)} 字符")

        # 分块处理
        print(f"\n[2/3] 调用 chonkie-fastapi 进行分块")
        chunks = chunk_text(text, args.api_url, args.chunk_size, args.chunk_overlap)

        # 插入数据库
        print(f"\n[3/3] 插入数据库")
        inserted_count = insert_chunks_to_db(
            args.db_url,
            args.table,
            chunks,
            args.source_id,
        )

        print("\n" + "=" * 60)
        print(f"完成! 成功插入 {inserted_count} 条记录到表 {args.table}")
        print("=" * 60)

        return 0

    except KeyboardInterrupt:
        print("\n\n操作已取消")
        return 1
    except Exception as e:
        print(f"\n错误: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
