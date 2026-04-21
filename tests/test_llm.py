import os
import dashscope

messages = [
    {'role': 'system', 'content': 'You are a helpful assistant.'},
    {'role': 'user', 'content': '你是谁？'}
]
response = dashscope.Generation.call(
    api_key=os.getenv("DASHSCOPE_API_KEY", "sk-229738edae18491c896d1699621e51a9"),
    model="qwen-plus",
    messages=messages,
    result_format='message'
)
print(response)

# import os
# from openai import OpenAI

# client = OpenAI(
#     # 若没有配置环境变量，请用百炼API Key将下行替换为：api_key="sk-xxx"
#     api_key=os.getenv("LLM_API_KEY"),
#     base_url=os.getenv("LLM_BASE_URL"),
#     timeout=120,
# )

# completion = client.chat.completions.create(
#     # 模型列表：https://help.aliyun.com/zh/model-studio/getting-started/models
#     model="qwen3.5-plus",
#     messages=[
#         {"role": "system", "content": "You are a helpful assistant."},
#         {"role": "user", "content": "你是谁？"},
#     ]
# )
# print(completion.model_dump_json())