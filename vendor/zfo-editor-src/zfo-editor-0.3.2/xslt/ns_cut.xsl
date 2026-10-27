<xsl:stylesheet xmlns:xsl="http://www.w3.org/1999/XSL/Transform" xmlns:fm="http://software602.cz/forms" xmlns:fo="http://www.w3.org/1999/XSL/Format" xmlns:dsig="http://www.w3.org/2000/09/xmldsig#" version="1.0" >
<xsl:output method="xml" encoding="utf-8" indent="yes"/>

<xsl:template match="/">
	<xsl:apply-templates/>
</xsl:template>


<xsl:template match="*">
	<xsl:element name="{local-name()}">
		<xsl:apply-templates select="@*|*|text()"/>
	</xsl:element>
</xsl:template>

<xsl:template match="@*">
	<xsl:attribute name="{local-name()}">
		<xsl:value-of select="."/>
	</xsl:attribute>
</xsl:template>

<xsl:template match="text()">
	<xsl:value-of select="."/>
</xsl:template>

</xsl:stylesheet>