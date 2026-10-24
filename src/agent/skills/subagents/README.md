subagent的技能树，如有多个子智能体，需要按照每个子智能体的名字创建对应目录，然后再其目录下，根据每一个技能的名字，创建技能名目录，最后内部必须有metadata.json  
SKILL.md  

示例
```text
skill/
    subagents/
        graphpainter/
            paint/
                metadata.json
                SKILL.md
            vision/
                metadata.json
                SKILL.md
```