/** Parse an SSE response body into the events it carries. */
export async function* sseEvents<T>(res: Response): AsyncGenerator<T> {
	const reader = res.body!.getReader();
	const decoder = new TextDecoder();
	let buffer = '';

	const parseLine = (line: string): T | null => {
		if (!line.startsWith('data: ')) return null;
		try {
			return JSON.parse(line.slice(6)) as T;
		} catch {
			return null;
		}
	};

	while (true) {
		const { done, value } = await reader.read();
		if (done) break;
		buffer += decoder.decode(value, { stream: true });
		const lines = buffer.split('\n');
		buffer = lines.pop()!;
		for (const line of lines) {
			const event = parseLine(line);
			if (event) yield event;
		}
	}

	const finalEvent = parseLine(buffer.trimEnd());
	if (finalEvent) yield finalEvent;
}
