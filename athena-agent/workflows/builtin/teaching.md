---
name: teaching
description: The Agent EFFECTIVELY teaches the Operator a subject or new skills — built on its own knowledge and the operator's ability to learn (learning style).
when: the operator asks to be taught, explained to, or built up on a subject/skill
safety: patient, adaptive, comprehension-validated
requirements:
  - label: learner_understood
    description: The operator's knowledge level + learning style are considered
    completed: false
  - label: lesson_given
    description: The lesson is built on the Agent's own knowledge and presented
    completed: false
  - label: comprehension_validated
    description: Comprehension is validated (question/quiz/step-back)
    completed: false
---

# Teaching Workflow

Teach the operator effectively — from YOUR knowledge, in THEIR learning
style, and prove they understood.

## 1.0 Understand the Learner
- THE RULE: assess the operator's current knowledge + learning style.
- THE WHY: teaching is adaptive, not a lecture.
- THE FAILURE: teaching the same way to everyone.
- THE EXIT: you know the starting level + the style.

### 1.1 Assess knowledge + style
- THE RULE: gauge what they know and how they learn (visual, hands-on,
  step-by-step, examples-first).
- THE WHY: the lesson must fit the learner.
- THE FAILURE: assuming the baseline.
- THE EXIT: level + style named.

### 1.2 Identify the subject + the goal
- THE RULE: state the subject and what "taught" looks like.
- THE WHY: the goal bounds the lesson.
- THE FAILURE: a wandering lesson.
- THE EXIT: the goal is one clear statement.

## 2.0 Teach
- THE RULE: build the lesson on your own knowledge, present it in the
  operator's style, use examples + steps + analogies.
- THE WHY: a lesson built on real understanding teaches; a list of facts
  doesn't.
- THE FAILURE: reciting documentation.
- THE EXIT: the material is presented in their style.

## 3.0 Validate
- THE RULE: check comprehension — a question, quiz, or step-back — and
  adjust where they stumbled.
- THE WHY: teaching isn't done until they can apply it.
- THE FAILURE: declaring victory after the explanation.
- THE EXIT: comprehension confirmed; the operator can apply it.

---

# Footer
The requirements this call MUST fulfill (the frontmatter checklist):
learner understood (level + style) · lesson given from own knowledge ·
comprehension validated. Fulfill every pending requirement before stopping.
---
