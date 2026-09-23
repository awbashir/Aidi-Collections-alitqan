import { useCallback } from 'react'

/* The categorical ramp walks navy -> azure -> steel -> lavender -> slate, all
 * close in hue so orange (held out of the set) still reads as the signal.
 * Kept in sync with the safelist in tailwind.config.cjs. */
const whiteListTwColor = [
    'navy-600',
    'ink-700',
    'azure-600',
    'azure-500',
    'lavender-500',
    'gray-600',
    'gray-700',
    'viz-3',
]

function useTwColorByName(prefix = 'bg'): (name: string) => string {
    const hashName = (name: string) => {
        let hash = 0
        for (let i = 0; i < name.length; i++) {
            const charCode = name.charCodeAt(i)
            hash += charCode
        }
        return hash
    }

    const generateTwColor = useCallback(
        (name: string) => {
            const hash = hashName(name)
            const index = hash % whiteListTwColor.length
            const color = whiteListTwColor[index]
            return `${prefix}-${color} dark:${prefix}-${color}`
        },
        [prefix]
    )

    return generateTwColor
}

export default useTwColorByName
