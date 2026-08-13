"""
大助理角色
"""

name = '大助理'
enabled = False
profile_id = 'api'

provider = 'deepseek'
api_url = ''
api_key = ''
model = 'deepseek-v4-flash'

max_context_length = 1024 * 1024

enable_hotwords = False
enable_thinking = False
enable_history = True
enable_read_selection = True
selection_max_length = 1000

output_mode = 'toast'
toast_initial_width = 0.5
toast_initial_height = 0
toast_font_family = '楷体'
toast_font_size = 23
toast_font_color = 'white'
toast_bg_color = '#075077'
toast_duration = 3000
toast_editable = True

temperature = 0.7
top_p = 0.9
max_tokens = 4096
stop = ''
extra_options = {}

prompt_prefix_hotwords = '热词列表：'
prompt_prefix_selection = '选中文字：'
prompt_prefix_input = '用户输入：'

system_prompt = '''
你是一个助手，帮助用户解答问题。

要求：
- 按用户的要求输出内容
- 不要添加任何额外说明
'''
