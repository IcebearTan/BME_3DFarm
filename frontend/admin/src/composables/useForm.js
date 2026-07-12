import { reactive } from 'vue'

export function useForm(fields, rules) {
  const errors = reactive({})

  function validate() {
    let ok = true
    for (const key in rules) {
      const value = fields[key]
      let msg = ''
      for (const rule of rules[key] || []) {
        const res = rule(value)
        if (res !== true) {
          msg = res || '校验失败'
          ok = false
          break
        }
      }
      errors[key] = msg
    }
    return ok
  }

  function clear(field) {
    if (field) errors[field] = ''
    else for (const k in errors) errors[k] = ''
  }

  return { errors, validate, clear }
}
