import jinja2


def render_greeting(name: str) -> str:
    return jinja2.Template("Hello {{ name }}!").render(name=name)
