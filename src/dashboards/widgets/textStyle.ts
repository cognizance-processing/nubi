/**
 * Typography for the text widget.
 *
 * props.fontSize (px), props.fontWeight and props.align set the type of the whole
 * block. props.heading marks a short block sitting tight in its tile: no vertical
 * padding and never a scrollbar. Long markdown keeps the default, which scrolls.
 */
export function textWidgetLook(props: Record<string, any> = {}): {
  className: string
  style: Record<string, string | number>
} {
  const style: Record<string, string | number> = {}
  const classes: string[] = []
  if (props.fontSize) {
    style.fontSize = props.fontSize
    // Markdown elements set their own size and leading, so they must inherit it.
    classes.push('[&_*]:[font-size:inherit]', '[&_*]:[line-height:inherit]')
  }
  if (props.fontWeight) {
    style.fontWeight = props.fontWeight
    classes.push('[&_*]:[font-weight:inherit]')
  }
  if (props.align) style.textAlign = props.align
  if (props.heading) {
    style.lineHeight = 1.2
    classes.push('overflow-hidden', 'px-3')
  } else {
    if (props.fontSize) style.lineHeight = 1.4
    classes.push('overflow-y-auto', 'px-5', 'py-3')
  }
  return { className: classes.join(' '), style }
}
