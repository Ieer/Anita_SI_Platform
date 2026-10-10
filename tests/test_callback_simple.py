#!/usr/bin/env python3
"""测试AI Q&A回调函数"""

def test_ai_callback_logic():
    """测试AI回调的逻辑"""
    print("=== 测试AI回调逻辑 ===")

    # 模拟_perform_ai_query函数的逻辑
    def _perform_ai_query(query, collection, min_score):
        if not query or not query.strip():
            return "请输入问题", []

        print(f"开始执行AI查询: query='{query}', collection='{collection}', min_score={min_score}")

        try:
            # 模拟API调用失败，回退到模拟数据
            print("模拟API调用失败，使用模拟回答")
            error_msg = "API 调用失败: 模拟错误"
            print(f"使用模拟回答: {error_msg}")

            answer = f"基于您的查询 '{query}'，AI 的回答是：这是一个模拟回答。{error_msg}"
            sources = [
                {
                    "title": "模拟来源文档 1",
                    "content": "文档内容摘要..."
                }
            ]
            return answer, sources

        except Exception as e:
            print(f"异常发生: {e}")
            return f"发生错误: {str(e)}", []

    # 测试用例
    test_cases = [
        ("什么是机器学习？", "test_collection", 0.5),
        ("", "test_collection", 0.5),
        ("   ", "test_collection", 0.5),
        ("测试问题", None, 0.8),
    ]

    for query, collection, min_score in test_cases:
        print(f"\n--- 测试用例: query='{query}', collection={collection}, min_score={min_score} ---")
        answer, sources = _perform_ai_query(query, collection, min_score)
        print(f"回答: {answer}")
        print(f"来源数量: {len(sources)}")

    print("\n✅ 回调逻辑测试完成")

if __name__ == "__main__":
    test_ai_callback_logic()