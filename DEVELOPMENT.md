# AI Classroom 开发规范与技术方案

## 1. 开发定位

本项目是一个基于开源 AI Tutor 项目进行二次开发的 AI 教育系统。

核心目标：

> 在尽可能复用已有代码的前提下，将通用 AI Tutor 改造为“计算机八股智能学习教室”。

后端和 AI 算法由 A 负责。

前端由其他成员负责。

---

## 2. 技术栈

### 2.1 编程语言

Python 3.x

---

### 2.2 Web 后端

FastAPI

用于：

- REST API
- Chat API
- Classroom API
- Student API
- Teacher API
- Resource API

---

### 2.3 ORM

优先复用原项目已有数据库访问方案。

如果没有合适方案：

```text
SQLAlchemy
```

---

### 2.4 数据库

MySQL

MySQL 主要保存：

```text
User
Classroom
ClassroomMember
Message
StudentProfile
LearningRecord
```

---

### 2.5 AI

使用外部 LLM API。

具体模型提供商根据实际开发环境决定。

禁止在项目中硬编码：

- API Key
- Secret
- Database Password

统一使用环境变量。

---

## 3. RAG

优先复用开源项目已有 RAG 实现。

第一版向量数据库：

> ChromaDB

如果原项目已经使用其他向量数据库，优先评估是否直接复用，而不是为了技术统一强行重写。

---

## 4. Agent

第一版：

```text
Teaching Agent
Teacher Resource Agent
```

---

### 4.1 Teaching Agent

职责：

- 接收学生问题
- 获取 StudentProfile
- 执行 RAG
- 构造 Prompt
- 调用 LLM
- 返回答案
- 记录学习行为

流程：

```text
Student
 ↓
Chat API
 ↓
Teaching Agent
 ├── StudentProfile
 ├── RAG
 └── LLM
 ↓
Answer
```

---

### 4.2 Teacher Resource Agent

职责：

- 接收教师教学主题
- 获取 Classroom 信息
- 检索知识库
- 生成教学资源
- 输出结构化内容

第一版只输出 PPT 大纲。

---

## 5. 代码架构

实际目录必须以开源项目真实结构为基础。

如果需要调整，推荐逐步演化到：

```text
backend/
├── app/
│   ├── main.py
│   │
│   ├── api/
│   │   ├── auth.py
│   │   ├── classroom.py
│   │   ├── chat.py
│   │   ├── student.py
│   │   └── resource.py
│   │
│   ├── models/
│   │   ├── user.py
│   │   ├── classroom.py
│   │   ├── message.py
│   │   ├── student_profile.py
│   │   └── learning_record.py
│   │
│   ├── schemas/
│   │
│   ├── services/
│   │   ├── chat_service.py
│   │   ├── profile_service.py
│   │   └── resource_service.py
│   │
│   ├── ai/
│   │   ├── llm_service.py
│   │   ├── prompts.py
│   │   └── agents/
│   │
│   ├── rag/
│   │   ├── indexer.py
│   │   └── retriever.py
│   │
│   └── core/
│       ├── config.py
│       └── database.py
│
├── knowledge/
│   └── java/
│
└── tests/
```

但是：

> **这只是目标结构，不允许为了符合该结构而大规模重构原项目。**

以原项目结构为基础逐步调整。

---

## 6. 数据模型

第一版推荐：

```text
User
```

字段：

```text
id
username
role
created_at
```

role：

```text
student
teacher
```

---

### Classroom

```text
id
name
subject
description
created_at
```

---

### ClassroomMember

```text
id
classroom_id
user_id
role
created_at
```

---

### Message

```text
id
classroom_id
user_id
role
content
created_at
```

role：

```text
student
assistant
teacher
```

---

### StudentProfile

```text
id
student_id
overall_level
learning_preference
knowledge_mastery
weak_points
updated_at
```

第一版可以使用 JSON 字段保存：

```text
knowledge_mastery
weak_points
```

如果数据库版本或 ORM 对 JSON 支持不理想，再采用 TEXT + JSON 序列化。

---

### LearningRecord

```text
id
student_id
classroom_id
topic
action
created_at
```

action 示例：

```text
ask_question
repeat_question
request_explanation
```

---

## 7. Chat 请求流程

标准流程：

```text
HTTP Request
    ↓
Chat API
    ↓
参数校验
    ↓
获取 User
    ↓
获取 Classroom
    ↓
获取 StudentProfile
    ↓
RAG Retrieval
    ↓
Teaching Agent
    ↓
Prompt
    ↓
LLM
    ↓
保存 Message
    ↓
Response
```

---

## 8. Prompt 设计

Prompt 不允许散落在业务代码中。

优先统一放在：

```text
prompts.py
```

或者：

```text
prompts/
├── teaching.txt
└── resource.txt
```

---

### Teaching Prompt

核心结构：

```text
角色定义

学生信息

学生知识掌握情况

学生薄弱点

学习偏好

检索到的知识

当前问题

回答要求
```

---

## 9. RAG 设计

知识文档：

```text
knowledge/
└── java/
    ├── hashmap.md
    ├── arraylist.md
    ├── jvm.md
    └── concurrent.md
```

metadata：

```json
{
  "subject": "computer_interview",
  "category": "java",
  "topic": "hashmap",
  "source": "hashmap.md"
}
```

---

## 10. 学生画像算法

第一版不实现复杂机器学习算法。

采用：

> 规则 + 学习行为统计

例如：

学生重复询问：

```text
HashMap
```

则增加：

```text
HashMap
```

相关薄弱点权重。

第一版可以只记录行为，不自动改变 mastery。

如果时间允许，再实现简单规则：

```text
重复提问：
mastery - 0.05

正确回答：
mastery + 0.10

连续多次正确：
mastery + 0.05
```

所有数值必须经过测试并在报告中说明其为 MVP 规则，不得描述为经过科学验证的 CDT 模型。

---

## 11. Agent 调度

第一版不要构建复杂 Multi-Agent Supervisor。

根据请求类型直接路由：

```text
学生 Chat
 ↓
Teaching Agent
```

```text
教师资源请求
 ↓
Teacher Resource Agent
```

如果原项目使用 LangGraph，则优先复用现有 Graph。

---

## 12. API 设计

推荐：

```text
GET /health

POST /classrooms
GET /classrooms/{classroom_id}

POST /classrooms/{classroom_id}/students
POST /classrooms/{classroom_id}/teachers
GET /classrooms/{classroom_id}/members

POST /chat
GET /chat/history

GET /students/{student_id}/profile
PUT /students/{student_id}/profile

POST /resources/ppt-outline
```

实际 API 应根据原项目结构调整。

---

## 13. 错误处理

至少处理：

```text
用户不存在
Classroom 不存在
StudentProfile 不存在
LLM 调用失败
RAG 检索失败
数据库连接失败
请求参数错误
```

AI API 调用失败时，不应该返回虚假的 AI 答案。

---

## 14. 环境变量

建议：

```text
LLM_API_KEY=
LLM_BASE_URL=
LLM_MODEL=

MYSQL_HOST=
MYSQL_PORT=
MYSQL_USER=
MYSQL_PASSWORD=
MYSQL_DATABASE=
```

使用：

```text
.env
```

`.env` 不得提交 Git。

必须提供：

```text
.env.example
```

---

## 15. 开发规则

每次修改前：

1. 阅读相关代码。
2. 找到调用链。
3. 确认影响范围。
4. 修改最少文件。
5. 运行测试。
6. 验证原功能没有被破坏。

禁止：

- 一次性重写整个项目
- 删除无法理解的代码
- 大规模改变依赖
- 未测试直接宣称完成
- 修改无关模块
- 硬编码 API Key
- 提交 `.env`

---

## 16. 开源项目使用规范

本项目使用开源项目作为技术参考和二次开发基础。

必须：

1. 检查原项目 LICENSE。
2. 保留必要的版权和许可证信息。
3. 在项目 README 中说明基础项目来源。
4. 不将开源项目原作者成果虚构为完全自主开发。
5. 记录本项目实际新增和修改的功能。

如果原项目 LICENSE 对二次分发有特殊要求，必须遵守。

---

## 17. 测试原则

至少包括：

### API Test

验证：

```text
Classroom
Chat
Profile
Resource
```

### RAG Test

验证：

```text
问题 → 检索结果
```

### AI Test

验证：

```text
问题 → AI回答
```

### Personalization Test

比较不同 StudentProfile 对相同问题的回答差异。

### Agent Test

验证：

```text
学生 → Teaching Agent
教师 → Resource Agent
```

---

## 18. 最终后端职责

A 负责：

```text
后端 API
数据库
RAG
Prompt
StudentProfile
Agent
LLM API
测试
接口文档
```

A 不负责：

```text
前端页面开发
UI设计
前端状态管理
前端动画
```

前后端通过 API 协作。
---
