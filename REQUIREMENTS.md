# AI Classroom 项目需求说明

## 1. 项目概述

### 1.1 项目名称

AI Classroom —— 面向计算机八股学习的 AI 智能教室。

### 1.2 项目定位

本项目是一个面向计算机专业学习与面试知识学习场景的 AI 智能教学系统。

系统以“一个教室 + 一个学科”为 MVP 范围，当前学科暂定为：

> 计算机八股 / 计算机基础面试知识

系统包含：

- 教师
- 多个学生
- AI 教学助手
- AI 知识库
- 学生学习画像
- 教师教学资源生成

前端主要采用聊天室形式呈现，后端负责用户、课堂、聊天、知识库、学生画像以及 AI 服务。

---

## 2. 项目开发原则

### 2.1 开源项目二次开发

本项目基于已有开源 AI Tutor / Tutor-Chatbot 项目进行二次开发。

严禁在没有必要的情况下从零重写已有功能。

开发前必须：

1. 分析原项目目录结构。
2. 确认原项目启动方式。
3. 确认已有 Chat、Agent、RAG 功能。
4. 尽可能复用已有实现。
5. 只对与本项目需求相关的部分进行修改或扩展。

### 2.2 MVP 优先

本项目开发周期约 3 周。

所有功能必须优先保证：

> 能运行 > 能演示 > 结构清晰 > 功能扩展

暂不追求大型商业系统级架构。

### 2.3 禁止无必要扩展

第一版暂不引入：

- Redis
- Kafka
- Elasticsearch
- Milvus
- Kubernetes
- 微服务
- Neo4j
- 复杂分布式架构
- 自训练大模型
- 复杂 CDT 数学模型
- 复杂权限系统

如果确实需要新增技术，必须先说明原因。

---

## 3. 用户角色

### 3.1 学生

学生可以：

1. 加入教室。
2. 查看当前教室信息。
3. 在聊天室向 AI 提问。
4. 查看历史聊天记录。
5. 获取基于个人学习画像生成的个性化回答。
6. 使用 AI 进行计算机八股学习。

---

### 3.2 教师

教师可以：

1. 创建或管理教室。
2. 查看教室学生。
3. 查看课堂聊天记录。
4. 向 AI 请求教学资源。
5. 输入一个知识主题，让 AI 生成教学 PPT 大纲。
6. 获取 AI 推荐的辅助学习资源。

第一版不要求真正生成 `.pptx` 文件。

第一版只需要生成结构化的 PPT 大纲。

---

### 3.3 AI 教学助手

AI 教学助手负责：

1. 回答学生问题。
2. 根据知识库提供可靠回答。
3. 根据学生画像调整回答难度。
4. 引用或说明知识来源。
5. 帮助教师生成教学资源。

第一版只实现两个主要 Agent：

### Teaching Agent

负责学生问答。

### Teacher Resource Agent

负责教师教学资源生成。

不要求第一版实现教师 Agent、助教 Agent、学伴 Agent、考官 Agent 四种独立 Agent。

---

## 4. 教室模型

系统 MVP 只需要支持一个或多个逻辑教室。

每个教室包含：

```text
Classroom
├── Teacher
├── Students
└── AI Teaching Assistant
```

教室需要记录：

- classroom_id
- name
- subject
- description
- teacher
- members
- created_at

当前 subject 默认：

> computer_interview

---

## 5. 聊天功能

### 5.1 学生聊天

学生发送：

```text
HashMap 为什么线程不安全？
```

后端处理流程：

```text
Student
 ↓
Chat API
 ↓
获取 StudentProfile
 ↓
RAG 检索
 ↓
Teaching Agent
 ↓
LLM
 ↓
Answer
 ↓
保存 Message
```

---

### 5.2 聊天记录

系统需要保存：

- 用户
- 教室
- 消息角色
- 消息内容
- 创建时间

至少支持：

```text
student message
assistant message
```

---

## 6. 计算机八股知识库

第一版只要求建立有限规模的高质量知识库。

建议首先支持：

```text
Java
├── Java基础
├── 集合
├── JVM
└── 并发
```

后续可以扩展：

```text
MySQL
Redis
Spring
计算机网络
操作系统
数据结构
```

但是第一版不要因为知识库规模而延误项目。

---

## 7. RAG 需求

系统必须支持：

```text
知识文档
 ↓
文本切分
 ↓
Embedding
 ↓
向量数据库
 ↓
相似度检索
 ↓
LLM
```

知识文档建议使用 Markdown。

每个知识块需要尽可能保留 metadata：

```json
{
  "subject": "computer_interview",
  "category": "java",
  "topic": "hashmap",
  "source": "xxx.md"
}
```

---

### 7.1 RAG 回答要求

当学生提出问题时：

1. 对问题进行检索。
2. 获取相关知识。
3. 将知识作为上下文传递给 LLM。
4. 尽量基于检索结果回答。
5. 如果知识库无法支持答案，不应该假装知识库中存在相关信息。

---

## 8. 学生画像

第一版不实现复杂的认知诊断模型。

采用可解释的规则型学生画像。

示例：

```json
{
  "student_id": 1,
  "overall_level": "beginner",
  "learning_preference": "example_first",
  "knowledge_mastery": {
    "HashMap": 0.3,
    "JVM": 0.6,
    "ConcurrentHashMap": 0.2
  },
  "weak_points": [
    "HashMap扩容",
    "并发安全"
  ]
}
```

画像至少包含：

- overall_level
- learning_preference
- knowledge_mastery
- weak_points
- updated_at

---

## 9. 个性化回答

学生提问时，AI 不仅获取问题，还应该获取学生画像。

完整输入：

```text
学生问题
+
学生水平
+
知识掌握度
+
薄弱知识点
+
学习偏好
+
RAG 检索结果
```

然后生成回答。

例如：

### 初级学生

重点：

- 简单语言
- 生活化例子
- 基础概念
- 少量专业术语

### 高级学生

重点：

- 底层原理
- 实现机制
- 面试追问
- 源码/性能相关内容

---

## 10. 学习行为

第一版至少记录：

- 学生提问
- 提问知识点
- 重复提问
- 学习主题
- 与 AI 的交互次数

这些数据为后续学生画像更新提供依据。

第一版可以先实现数据记录，不要求复杂的机器学习模型。

---

## 11. 教师资源生成

教师输入：

```text
HashMap
```

AI 返回结构化教学资源：

```json
{
  "title": "HashMap 原理与常见面试题",
  "objectives": [],
  "sections": [],
  "key_points": [],
  "examples": [],
  "questions": [],
  "summary": []
}
```

第一版：

> 只生成 PPT 大纲，不生成真实 PPT 文件。

---

## 12. 辅助视频

系统后续可以支持辅助视频推荐。

第一版允许使用：

- 预配置视频资源
- 外部视频搜索结果
- 视频标题
- 视频简介
- 视频链接

不要求第一版实现复杂推荐算法。

---

## 13. 后端 API 最低要求

至少支持以下接口类别：

```text
/auth

/classrooms

/users

/students

/profile

/chat

/resources
```

核心 API 示例：

```http
POST /classrooms
GET /classrooms/{id}

POST /classrooms/{id}/students
GET /classrooms/{id}/members

POST /chat
GET /chat/history

GET /students/{id}/profile

POST /resources/ppt-outline
```

实际路径应根据原开源项目 API 结构合理调整。

---

## 14. 非功能需求

### 14.1 可维护性

代码需要按照职责划分：

```text
API
Service
Model
Schema
AI
RAG
Agent
```

避免将所有逻辑写进单个文件。

---

### 14.2 可测试性

至少测试：

- API
- MySQL CRUD
- RAG 检索
- AI Chat
- StudentProfile
- 个性化 Prompt
- Teacher Resource Agent

---

### 14.3 可演示性

最终 Demo 至少可以演示：

### 学生

```text
进入教室
 ↓
提问
 ↓
AI 检索知识库
 ↓
根据学生画像回答
 ↓
保存学习记录
```

### 教师

```text
进入教室
 ↓
输入教学主题
 ↓
AI 检索知识库
 ↓
生成 PPT 大纲
```

---

## 15. MVP 完成标准

满足以下条件即可认为 MVP 完成：

- [ ] 后端能够启动
- [ ] MySQL 能正常连接
- [ ] 教师和学生能够区分
- [ ] Classroom 能创建
- [ ] 学生能够加入 Classroom
- [ ] 聊天 API 正常
- [ ] 聊天记录能够保存
- [ ] 计算机八股知识库能够检索
- [ ] RAG 能够辅助回答
- [ ] StudentProfile 能够查询
- [ ] StudentProfile 能够影响 Prompt
- [ ] Teaching Agent 能正常回答
- [ ] Teacher Resource Agent 能生成 PPT 大纲
- [ ] 前端能够正常调用后端
- [ ] 基本测试完成
- [ ] GitHub 代码能够运行
- [ ] 项目文档完成
---
