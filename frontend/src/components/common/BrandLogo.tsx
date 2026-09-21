import { cn } from 'cn'

// 标识是透明底位图，浅深主题共用一张，不再按主题换图。
export function BrandLogo({
  size = 32,
  label = '',
  className,
}: {
  /** 边长（像素），图形本身为正方形 */
  size?: number
  /** 无障碍名称；留空表示纯装饰，由相邻的品牌文字承担朗读 */
  label?: string
  className?: string
}) {
  return (
    <img
      src="/brand/logo.png"
      alt={label}
      width={size}
      height={size}
      className={cn('block shrink-0', className)}
    />
  )
}
