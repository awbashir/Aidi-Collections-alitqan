import type { ComponentPropsWithoutRef } from 'react'

interface FormDesriptionProps extends ComponentPropsWithoutRef<'div'> {
    title: string
    desc: string
}

const FormDesription = ({ title, desc, ...rest }: FormDesriptionProps) => {
    return (
        <div {...rest}>
            <h5 className="mb-1">{title}</h5>
            {desc ? <p className="text-gray-600">{desc}</p> : null}
        </div>
    )
}

export default FormDesription
