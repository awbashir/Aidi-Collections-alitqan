import { useEffect } from 'react'
import { setDirection, useAppDispatch, useAppSelector } from '@/store'
import type { Direction } from '@/@types/theme'

function useDirection(): [
    direction: Direction,
    updateDirection: (dir: Direction) => void
] {
    const direction = useAppSelector((state) => state.theme.direction)

    const dispatch = useAppDispatch()
    const updateDirection = (dir: Direction) => {
        dispatch(setDirection(dir))
        // Save to localStorage
        localStorage.setItem('theme-direction', dir)

    }
   // Initialize direction from localStorage on mount
    useEffect(() => {
        const savedDirection = localStorage.getItem('theme-direction') as Direction
        if (savedDirection && (savedDirection === 'ltr' || savedDirection === 'rtl')) {
            dispatch(setDirection(savedDirection))
        }
    }, [dispatch])

    useEffect(() => {
        if (window === undefined) {
            return
        }
        const root = window.document.documentElement
        root.setAttribute('dir', direction)
    }, [direction])

    return [direction, updateDirection]
}

export default useDirection
