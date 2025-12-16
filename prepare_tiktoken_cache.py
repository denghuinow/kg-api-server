#!/usr/bin/env python3
"""
预下载 tiktoken 编码文件到本地缓存目录
用于解决网络环境下的 SSL 连接问题
"""
import os
import sys
from pathlib import Path

def prepare_tiktoken_cache(cache_dir: str = None):
    """
    预下载 tiktoken 编码文件
    
    Args:
        cache_dir: 缓存目录路径，默认为 ./data/tiktoken_cache
    """
    if cache_dir is None:
        cache_dir = os.path.join(os.path.dirname(__file__), "data", "tiktoken_cache")
    
    cache_path = Path(cache_dir)
    cache_path.mkdir(parents=True, exist_ok=True)
    
    # 设置环境变量
    os.environ["TIKTOKEN_CACHE_DIR"] = str(cache_path.absolute())
    
    print(f"📁 缓存目录: {cache_path.absolute()}")
    print("📥 开始下载 tiktoken 编码文件...")
    
    try:
        import tiktoken
    except ImportError:
        print("❌ 错误: 未安装 tiktoken")
        print("   请运行: pip install tiktoken")
        sys.exit(1)
    
    # 需要下载的编码列表
    encodings = ["cl100k_base"]  # 默认使用的编码
    
    success_count = 0
    failed_encodings = []
    
    for encoding_name in encodings:
        try:
            print(f"  ⬇️  下载 {encoding_name}...", end=" ", flush=True)
            encoding = tiktoken.get_encoding(encoding_name)
            # 触发下载
            encoding.encode("test")
            print("✅ 完成")
            success_count += 1
        except Exception as e:
            print(f"❌ 失败: {e}")
            failed_encodings.append((encoding_name, str(e)))
    
    print("\n" + "=" * 60)
    print(f"✅ 成功下载 {success_count}/{len(encodings)} 个编码文件")
    
    if failed_encodings:
        print(f"\n❌ 失败 {len(failed_encodings)} 个:")
        for name, error in failed_encodings:
            print(f"   - {name}: {error}")
    
    print(f"\n📂 缓存位置: {cache_path.absolute()}")
    print("\n💡 使用方法:")
    print("   1. 在 docker-compose.yml 中已配置挂载此目录")
    print("   2. 或设置环境变量: export TIKTOKEN_CACHE_DIR=" + str(cache_path.absolute()))
    
    return success_count, failed_encodings


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description="预下载 tiktoken 编码文件")
    parser.add_argument(
        "--cache-dir",
        type=str,
        default=None,
        help="缓存目录路径（默认: ./data/tiktoken_cache）"
    )
    
    args = parser.parse_args()
    
    try:
        success, failed = prepare_tiktoken_cache(args.cache_dir)
        sys.exit(0 if not failed else 1)
    except KeyboardInterrupt:
        print("\n\n⚠️  用户中断")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ 错误: {e}")
        sys.exit(1)

