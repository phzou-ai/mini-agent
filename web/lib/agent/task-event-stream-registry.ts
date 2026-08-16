export type ClosableTaskEventStream = {
  close: () => void
}

export class TaskEventStreamRegistry {
  private readonly streams = new Map<string, ClosableTaskEventStream>()

  replace(taskId: string, stream: ClosableTaskEventStream) {
    const current = this.streams.get(taskId)
    if (current === stream) return

    current?.close()
    this.streams.set(taskId, stream)
  }

  close(taskId: string, expected?: ClosableTaskEventStream) {
    const current = this.streams.get(taskId)
    if (!current || (expected && current !== expected)) return false

    this.streams.delete(taskId)
    current.close()
    return true
  }

  closeAll() {
    for (const stream of this.streams.values()) {
      stream.close()
    }
    this.streams.clear()
  }

  has(taskId: string) {
    return this.streams.has(taskId)
  }

  get size() {
    return this.streams.size
  }
}
